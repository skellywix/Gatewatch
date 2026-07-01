#!/usr/bin/env python3
"""Update Gatewatch from the GitHub source archive without touching app data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_BRANCH = "main"
DEFAULT_SOURCE_URL = "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"
REQUIRED_SOURCE_FILES = (
    "app.py",
    "web/index.html",
    "web/app.js",
    "web/theme.js",
    "web/styles.css",
    "scripts/update_gatewatch.py",
    "scripts/gatewatch-entrypoint.py",
)
COPY_PATHS = (
    "app.py",
    "README.md",
    "Dockerfile",
    "web",
    "scripts",
    "deploy",
    "docs",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_data_dir() -> Path:
    configured_db = os.environ.get("GATEWATCH_DB", "").strip()
    if configured_db:
        return Path(configured_db).expanduser().parent
    if Path("/data").exists():
        return Path("/data")
    return Path("/var/lib/gatewatch")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_dir = default_data_dir()
    parser = argparse.ArgumentParser(description="Safely update Gatewatch from GitHub.")
    parser.add_argument("--mode", choices=("auto", "volume", "systemd"), default=os.environ.get("GATEWATCH_UPDATE_MODE", "auto"))
    parser.add_argument("--branch", default=os.environ.get("GATEWATCH_UPDATE_BRANCH", DEFAULT_BRANCH))
    parser.add_argument("--source-url", default=os.environ.get("GATEWATCH_UPDATE_SOURCE_URL", DEFAULT_SOURCE_URL))
    parser.add_argument("--data-dir", default=os.environ.get("GATEWATCH_UPDATE_DATA_DIR", str(data_dir)))
    parser.add_argument("--install-dir", default=os.environ.get("GATEWATCH_UPDATE_INSTALL_DIR", "/opt/gatewatch"))
    parser.add_argument("--service-name", default=os.environ.get("GATEWATCH_UPDATE_SERVICE_NAME", "gatewatch"))
    parser.add_argument("--status-file", default=os.environ.get("GATEWATCH_UPDATE_STATUS_FILE", str(data_dir / "gatewatch-update-status.json")))
    parser.add_argument("--log-file", default=os.environ.get("GATEWATCH_UPDATE_LOG_FILE", str(data_dir / "gatewatch-update.log")))
    parser.add_argument("--restart-process", action="store_true", default=os.environ.get("GATEWATCH_UPDATE_RESTART_PROCESS", "") in {"1", "true", "yes"})
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Accepted for non-interactive operator parity.")
    return parser.parse_args(argv)


class UpdateRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.data_dir = Path(args.data_dir).expanduser()
        self.install_dir = Path(args.install_dir).expanduser()
        self.status_file = Path(args.status_file).expanduser()
        self.log_file = Path(args.log_file).expanduser()
        self.started_at = utc_now()
        self.lock_dir = self.data_dir / ".gatewatch-update.lock"

    def log(self, message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line, flush=True)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def write_status(self, state: str, message: str, **extra) -> None:
        payload = {
            "state": state,
            "message": message,
            "startedAt": self.started_at,
            "updatedAt": utc_now(),
            "mode": self.args.mode,
            "branch": self.args.branch,
            "sourceUrl": self.args.source_url,
            "dataDir": str(self.data_dir),
            "installDir": str(self.install_dir),
            "serviceName": self.args.service_name,
            "logFile": str(self.log_file),
        }
        payload.update(extra)
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.status_file.with_name(f".{self.status_file.name}.{os.getpid()}.tmp")
            temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temp_path, self.status_file)
        except OSError as exc:
            self.log(f"could not write status file {self.status_file}: {exc}")

    def acquire_lock(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_dir.mkdir()
        except FileExistsError as exc:
            raise SystemExit(f"another Gatewatch update appears to be running: {self.lock_dir}") from exc

    def release_lock(self) -> None:
        try:
            self.lock_dir.rmdir()
        except OSError:
            pass


def validate_branch(branch: str) -> str:
    branch = branch.strip()
    if not branch:
        raise SystemExit("--branch is required")
    if len(branch) > 120 or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise SystemExit("--branch contains unsupported characters")
    if branch.startswith(("-", "/", ".")) or branch.endswith("/") or ".." in branch:
        raise SystemExit("--branch must be a normal GitHub branch name")
    return branch


def source_url_for_branch(branch: str) -> str:
    return f"https://github.com/skellywix/Gatewatch/archive/refs/heads/{branch}.tar.gz"


def validate_source_url(value: str, branch: str) -> str:
    value = value.strip() or source_url_for_branch(branch)
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise SystemExit("--source-url must be an https://github.com URL")
    expected_prefix = "/skellywix/Gatewatch/archive/refs/heads/"
    if not parsed.path.startswith(expected_prefix) or not parsed.path.endswith(".tar.gz"):
        raise SystemExit("--source-url must point to the skellywix/Gatewatch branch archive")
    if any(char in value for char in "\r\n\t"):
        raise SystemExit("--source-url contains unsupported whitespace")
    return value


def detect_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if Path("/.dockerenv").exists() or Path("/data").exists():
        return "volume"
    return "systemd"


def safe_extract_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue
            relative = Path(*parts[1:])
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"unsafe archive member path: {member.name}")
            target = destination / relative
            try:
                target.resolve(strict=False).relative_to(destination_resolved)
            except ValueError as exc:
                raise SystemExit(f"archive member escapes destination: {member.name}") from exc
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise SystemExit(f"refusing non-file archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            try:
                os.chmod(target, member.mode & 0o777)
            except OSError:
                pass


def validate_source_tree(source_dir: Path) -> None:
    missing = [name for name in REQUIRED_SOURCE_FILES if not (source_dir / name).is_file()]
    if missing:
        raise SystemExit(f"Gatewatch source archive is missing: {', '.join(missing)}")


def download_source(run: UpdateRun) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="gatewatch-update."))
    archive_path = temp_dir / "gatewatch.tar.gz"
    source_dir = temp_dir / "source"
    run.log(f"downloading {run.args.source_url}")
    request = urllib.request.Request(run.args.source_url, headers={"User-Agent": "GatewatchUpdater/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (OSError, urllib.error.URLError) as exc:
        raise SystemExit(f"could not download Gatewatch update: {exc}") from exc
    safe_extract_archive(archive_path, source_dir)
    validate_source_tree(source_dir)
    return source_dir


def backup_database(run: UpdateRun) -> str:
    configured_db = os.environ.get("GATEWATCH_DB", "").strip()
    db_path = Path(configured_db).expanduser() if configured_db else run.data_dir / "gatewatch.db"
    if not db_path.exists():
        run.log(f"database backup skipped; {db_path} does not exist yet")
        return ""
    backup_dir = run.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"gatewatch-preupdate-{stamp}.db"
    shutil.copy2(db_path, backup_path)
    run.log(f"database backup written to {backup_path}")
    return str(backup_path)


def copy_source_path(source_dir: Path, target_dir: Path, relative_name: str) -> None:
    source = source_dir / relative_name
    target = target_dir / relative_name
    if not source.exists():
        return
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def install_volume_release(run: UpdateRun, source_dir: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    releases_dir = run.data_dir / "releases"
    release_dir = releases_dir / stamp
    release_dir.mkdir(parents=True, exist_ok=False)
    for name in COPY_PATHS:
        copy_source_path(source_dir, release_dir, name)
    marker = run.data_dir / "current-release.txt"
    temp_marker = run.data_dir / f".current-release.{os.getpid()}.tmp"
    temp_marker.write_text(str(release_dir) + "\n", encoding="utf-8")
    os.replace(temp_marker, marker)
    run.log(f"staged release in persistent data volume: {release_dir}")
    return str(release_dir)


def install_systemd_release(run: UpdateRun, source_dir: Path) -> str:
    run.install_dir.mkdir(parents=True, exist_ok=True)
    for name in COPY_PATHS:
        copy_source_path(source_dir, run.install_dir, name)
    run.log(f"installed source files into {run.install_dir}")
    return str(run.install_dir)


def wait_for_health(port: str = "8087") -> None:
    url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - command-line updater reports best evidence
            last_error = exc
            time.sleep(1)
    raise SystemExit(f"Gatewatch did not become healthy at {url}: {last_error}")


def restart_systemd(run: UpdateRun) -> None:
    run.log(f"restarting systemd service {run.args.service_name}")
    subprocess.run(["systemctl", "restart", run.args.service_name], check=True)
    wait_for_health(os.environ.get("GATEWATCH_PORT", "8087"))


def queue_process_restart(run: UpdateRun) -> None:
    restart_pid = os.environ.get("GATEWATCH_UPDATE_PARENT_PID", "1").strip() or "1"
    run.log(f"queueing process restart for pid {restart_pid}")
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, signal, sys, time; time.sleep(1); os.kill(int(sys.argv[1]), signal.SIGTERM)",
            restart_pid,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.branch = validate_branch(args.branch)
    args.source_url = validate_source_url(args.source_url, args.branch)
    args.mode = detect_mode(args.mode)
    run = UpdateRun(args)
    run.acquire_lock()
    backup_path = ""
    release_path = ""
    try:
        run.write_status("running", "Downloading Gatewatch update from GitHub.")
        run.log(f"starting Gatewatch update in {args.mode} mode")
        source_dir = download_source(run)
        backup_path = backup_database(run)
        if args.dry_run:
            run.write_status("succeeded", "Dry run passed; no files were changed.", backupPath=backup_path, releasePath="")
            return 0
        if args.mode == "volume":
            release_path = install_volume_release(run, source_dir)
            message = "Update staged in the persistent data volume."
            if args.restart_process:
                message = "Update staged. Restarting Gatewatch to load it."
                run.write_status("restart_queued", message, backupPath=backup_path, releasePath=release_path)
                queue_process_restart(run)
            else:
                run.write_status("succeeded", message, backupPath=backup_path, releasePath=release_path)
            return 0
        release_path = install_systemd_release(run, source_dir)
        if args.restart_process:
            run.write_status(
                "restart_queued",
                "Update installed. Restarting Gatewatch to load it.",
                backupPath=backup_path,
                releasePath=release_path,
            )
            queue_process_restart(run)
            return 0
        restart_systemd(run)
        run.write_status(
            "succeeded",
            "Update installed from GitHub and the Gatewatch service is healthy.",
            backupPath=backup_path,
            releasePath=release_path,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level command should persist failure state
        run.log(f"update failed: {exc}")
        run.write_status(
            "failed",
            str(exc),
            backupPath=backup_path,
            releasePath=release_path,
            exitCode=1,
        )
        return 1
    finally:
        run.release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
