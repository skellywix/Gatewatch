#!/usr/bin/env python3
"""Build, run, health-check, and tear down a local Gatewatch mock deployment."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
DEFAULT_SOURCE_URL = "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"
DEFAULT_IMAGE = "gatewatch-mock:latest"
DEFAULT_CONTAINER = "gatewatch-mock"
DEFAULT_VOLUME = "gatewatch-mock-data"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18087
DEFAULT_WORK_DIR = REPO_ROOT / "output" / "mock-deployment"
WORK_DIR_MARKER = ".gatewatch-mock-workdir"


def display_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run(
    command: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    echo_output: bool = True,
    display: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"$ {display_command(display or command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if capture and echo_output and result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if check and result.returncode:
        raise SystemExit(f"command failed with exit code {result.returncode}: {display_command(command)}")
    return result


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker is required for mock deployment commands")


def positive_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a port from 1 to 65535") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("must be a port from 1 to 65535")
    return port


def reset_work_dir(work_dir: Path) -> Path:
    resolved = work_dir.resolve()
    if resolved.exists():
        marker = resolved / WORK_DIR_MARKER
        if not marker.exists():
            raise SystemExit(f"refusing to remove unmarked work directory: {resolved}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / WORK_DIR_MARKER).write_text("Gatewatch mock deployment work directory\n", encoding="utf-8")
    return resolved


def remove_work_dir(work_dir: Path) -> None:
    resolved = work_dir.resolve()
    if not resolved.exists():
        return
    marker = resolved / WORK_DIR_MARKER
    if not marker.exists():
        raise SystemExit(f"refusing to remove unmarked work directory: {resolved}")
    shutil.rmtree(resolved)


def ensure_safe_archive_member(destination: Path, member: tarfile.TarInfo) -> None:
    if member.issym() or member.islnk():
        raise SystemExit(f"refusing linked archive member: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise SystemExit(f"refusing non-file archive member: {member.name}")
    target = (destination / member.name).resolve()
    destination_resolved = destination.resolve()
    try:
        target.relative_to(destination_resolved)
    except ValueError as exc:
        raise SystemExit(f"refusing archive member outside extraction directory: {member.name}") from exc


def safe_extract(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            ensure_safe_archive_member(destination, member)
        try:
            archive.extractall(destination, members=members, filter="data")
        except TypeError:
            # Python builds without the PEP 706 extraction-filter backport
            # (pre 3.10.12 / 3.11.4). Members were already vetted above.
            archive.extractall(destination, members=members)


def source_root_from_extract(extract_dir: Path) -> Path:
    if (extract_dir / "Dockerfile").exists():
        return extract_dir
    children = [child for child in extract_dir.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / "Dockerfile").exists():
        return children[0]
    raise SystemExit(f"extracted source does not contain a Gatewatch Dockerfile: {extract_dir}")


def inspect_gatewatch_source(source_root: Path) -> None:
    required = [
        "Dockerfile",
        "app.py",
        "README.md",
        "web/index.html",
        "web/app.js",
        "web/styles.css",
    ]
    missing = [path for path in required if not (source_root / path).exists()]
    if missing:
        raise SystemExit(f"source archive is missing required app file(s): {', '.join(missing)}")
    print(f"Source inspection passed: {source_root}")


def download_source(source_url: str, work_dir: Path) -> Path:
    source_area = work_dir / "source"
    source_area.mkdir(parents=True, exist_ok=True)
    archive_path = work_dir / "gatewatch-source.tar.gz"
    print(f"Downloading Gatewatch source: {source_url}")
    urllib.request.urlretrieve(source_url, archive_path)
    safe_extract(archive_path, source_area)
    source_root = source_root_from_extract(source_area)
    inspect_gatewatch_source(source_root)
    return source_root


def docker_names(command: list[str]) -> list[str]:
    result = run(command, capture=True, echo_output=False)
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def container_exists(name: str) -> bool:
    return name in docker_names(["docker", "ps", "-a", "--format", "{{.Names}}"])


def volume_exists(name: str) -> bool:
    return name in docker_names(["docker", "volume", "ls", "--format", "{{.Name}}"])


def normalized_image_name(name: str) -> str:
    if "@" in name:
        return name
    final_segment = name.rsplit("/", 1)[-1]
    if ":" in final_segment:
        return name
    return f"{name}:latest"


def image_exists(name: str) -> bool:
    return normalized_image_name(name) in docker_names(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])


def remove_container(name: str) -> None:
    if container_exists(name):
        run(["docker", "rm", "-f", name])


def remove_volume(name: str) -> None:
    if volume_exists(name):
        run(["docker", "volume", "rm", name])


def remove_image(name: str) -> None:
    if image_exists(name):
        run(["docker", "image", "rm", name])


def build_image(source_root: Path, image_name: str) -> None:
    run(["docker", "build", "-t", image_name, str(source_root)])


def run_container(args: argparse.Namespace) -> None:
    session_secret = secrets.token_urlsafe(48)
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        args.container_name,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-p",
        f"{args.host}:{args.port}:8087",
        "-v",
        f"{args.volume_name}:/data",
        "-e",
        "GATEWATCH_HOST=0.0.0.0",
        "-e",
        "GATEWATCH_PORT=8087",
        "-e",
        "GATEWATCH_DB=/data/gatewatch.db",
        "-e",
        "GATEWATCH_CONFIG_FILE=/data/gatewatch.env",
        "-e",
        "GATEWATCH_ALLOW_INSECURE_NETWORK=1",
        "-e",
        f"GATEWATCH_SESSION_SECRET={session_secret}",
        args.image_name,
    ]
    display = [
        "GATEWATCH_SESSION_SECRET=<generated>" if item.startswith("GATEWATCH_SESSION_SECRET=") else item
        for item in command
    ]
    run(
        command,
        display=display,
    )


def health_url(args: argparse.Namespace) -> str:
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return f"http://{host}:{args.port}/healthz"


def wait_for_http_health(url: str, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8", "replace")
                if 200 <= response.status < 300:
                    print(f"HTTP health check passed: {url}")
                    print(body)
                    return body
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise SystemExit(f"HTTP health check failed for {url}: {last_error}")


def docker_health_status(container_name: str) -> str:
    result = run(
        [
            "docker",
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container_name,
        ],
        capture=True,
    )
    return (result.stdout or "").strip().splitlines()[-1]


def wait_for_docker_health(container_name: str, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    last_status = "unknown"
    while time.monotonic() < deadline:
        last_status = docker_health_status(container_name)
        if last_status in {"healthy", "none"}:
            print(f"Docker health check passed: {container_name}={last_status}")
            return last_status
        if last_status == "unhealthy":
            run(["docker", "logs", "--tail", "80", container_name], check=False)
            raise SystemExit(f"Docker health check failed: {container_name}=unhealthy")
        time.sleep(2)
    raise SystemExit(f"Docker health check timed out: {container_name}={last_status}")


def command_inspect_package(_: argparse.Namespace) -> int:
    manifest_path = PACKAGE_DIR / "PACKAGE_MANIFEST.json"
    readme_path = PACKAGE_DIR / "README.md"
    script_path = PACKAGE_DIR / "mock_deploy.py"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_files = manifest.get("files", [])
    missing = [name for name in required_files if not (PACKAGE_DIR / name).exists()]
    if missing:
        raise SystemExit(f"package manifest references missing file(s): {', '.join(missing)}")

    readme = readme_path.read_text(encoding="utf-8")
    script = script_path.read_text(encoding="utf-8")
    required_readme_text = [
        "Mock Deployment Checklist",
        "python deploy\\mock-local\\mock_deploy.py deploy --reset-data",
        "python deploy\\mock-local\\mock_deploy.py health",
        "python deploy\\mock-local\\mock_deploy.py teardown",
        "/healthz",
    ]
    required_script_text = [
        DEFAULT_SOURCE_URL,
        "GATEWATCH_DB=/data/gatewatch.db",
        "--read-only",
        "no-new-privileges",
        "safe_extract",
    ]
    missing_readme = [text for text in required_readme_text if text not in readme]
    missing_script = [text for text in required_script_text if text not in script]
    if missing_readme:
        raise SystemExit(f"package guide is missing required text: {', '.join(missing_readme)}")
    if missing_script:
        raise SystemExit(f"mock deploy helper is missing required text: {', '.join(missing_script)}")
    if manifest.get("default_source_url") != DEFAULT_SOURCE_URL:
        raise SystemExit("package manifest default_source_url does not match the helper default")

    print(f"Package inspection passed: {PACKAGE_DIR.relative_to(REPO_ROOT)}")
    print(f"Default source: {manifest['default_source_url']}")
    print(f"Files: {', '.join(required_files)}")
    return 0


def command_deploy(args: argparse.Namespace) -> int:
    require_docker()
    work_dir = reset_work_dir(args.work_dir)
    source_root = download_source(args.source_url, work_dir)
    build_image(source_root, args.image_name)
    remove_container(args.container_name)
    if args.reset_data or not args.keep_data:
        remove_volume(args.volume_name)
    run(["docker", "volume", "create", args.volume_name])
    run_container(args)
    if not args.keep_source:
        remove_work_dir(args.work_dir)
    command_health(args)
    print(f"Mock deployment ready: http://{args.host}:{args.port}")
    return 0


def command_health(args: argparse.Namespace) -> int:
    require_docker()
    if not container_exists(args.container_name):
        raise SystemExit(f"mock container is not running: {args.container_name}")
    wait_for_http_health(health_url(args), args.timeout)
    wait_for_docker_health(args.container_name, args.timeout)
    print("Mock deployment health verification passed")
    return 0


def command_teardown(args: argparse.Namespace) -> int:
    require_docker()
    if not args.verify_only:
        remove_container(args.container_name)
        remove_volume(args.volume_name)
        if not args.keep_image:
            remove_image(args.image_name)
        remove_work_dir(args.work_dir)

    failures: list[str] = []
    if container_exists(args.container_name):
        failures.append(f"container still exists: {args.container_name}")
    if volume_exists(args.volume_name):
        failures.append(f"volume still exists: {args.volume_name}")
    if not args.keep_image and image_exists(args.image_name):
        failures.append(f"image still exists: {normalized_image_name(args.image_name)}")
    if args.work_dir.resolve().exists():
        failures.append(f"work directory still exists: {args.work_dir.resolve()}")
    if failures:
        raise SystemExit("; ".join(failures))
    if args.keep_image:
        print("Teardown verification passed: mock container, volume, and work directory are absent")
    else:
        print("Teardown verification passed: mock container, image, volume, and work directory are absent")
    return 0


def add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER)
    parser.add_argument("--image-name", default=DEFAULT_IMAGE)
    parser.add_argument("--volume-name", default=DEFAULT_VOLUME)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=positive_port, default=DEFAULT_PORT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--timeout", type=int, default=90)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-package", help="Validate the reusable package files.")
    inspect_parser.set_defaults(func=command_inspect_package)

    deploy_parser = subparsers.add_parser("deploy", help="Build and run a local mock deployment.")
    add_runtime_options(deploy_parser)
    deploy_parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    deploy_parser.add_argument("--reset-data", action="store_true", help="Remove the mock Docker volume before start.")
    deploy_parser.add_argument("--keep-data", action="store_true", help="Reuse the existing mock Docker volume.")
    deploy_parser.add_argument("--keep-source", action="store_true", help="Keep the downloaded source under the work dir.")
    deploy_parser.set_defaults(func=command_deploy)

    health_parser = subparsers.add_parser("health", help="Check HTTP and Docker health for the running mock.")
    add_runtime_options(health_parser)
    health_parser.set_defaults(func=command_health)

    teardown_parser = subparsers.add_parser("teardown", help="Remove mock runtime artifacts and verify cleanup.")
    add_runtime_options(teardown_parser)
    teardown_parser.add_argument("--keep-image", action="store_true", help="Do not remove or verify the Docker image.")
    teardown_parser.add_argument("--verify-only", action="store_true", help="Only verify artifacts are absent.")
    teardown_parser.set_defaults(func=command_teardown)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
