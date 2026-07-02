#!/usr/bin/env python3
"""Start Gatewatch from the current persistent release when one is staged."""

from __future__ import annotations

import os
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_RELEASE_FILES = (
    "app.py",
    "web/index.html",
    "web/app.js",
    "web/theme.js",
    "web/styles.css",
    "scripts/update_gatewatch.py",
    "scripts/gatewatch-entrypoint.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_release_app(marker_path: Path) -> Path | None:
    try:
        release_text = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not release_text:
        return None
    release_dir = Path(release_text)
    data_dir = Path(os.environ.get("GATEWATCH_UPDATE_DATA_DIR", "/data")).resolve()
    try:
        release_dir.resolve(strict=False).relative_to((data_dir / "releases").resolve(strict=False))
    except ValueError:
        return None
    missing = [name for name in REQUIRED_RELEASE_FILES if not (release_dir / name).is_file()]
    if missing:
        mark_release_failed(data_dir, release_dir, missing)
        return None
    return release_dir / "app.py"


def mark_release_failed(data_dir: Path, release_dir: Path, missing: list[str]) -> None:
    status_path = Path(os.environ.get("GATEWATCH_UPDATE_STATUS_FILE", data_dir / "gatewatch-update-status.json"))
    message = f"Staged release is incomplete and was not loaded: {', '.join(missing)}"
    try:
        current = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    current.update(
        {
            "state": "failed",
            "message": message,
            "updatedAt": utc_now(),
            "releasePath": str(release_dir),
        }
    )
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass


def mark_release_loaded(data_dir: Path, app_path: Path) -> None:
    status_path = Path(os.environ.get("GATEWATCH_UPDATE_STATUS_FILE", data_dir / "gatewatch-update-status.json"))
    try:
        current = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    if current.get("state") not in {"running", "restart_queued"}:
        return
    current.update(
        {
            "state": "succeeded",
            "message": "Update loaded from the persistent data volume.",
            "updatedAt": utc_now(),
            "releasePath": str(app_path.parent),
        }
    )
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> None:
    data_dir = Path(os.environ.get("GATEWATCH_UPDATE_DATA_DIR", "/data"))
    marker_path = data_dir / "current-release.txt"
    app_path = valid_release_app(marker_path) or Path("/app/app.py")
    if app_path != Path("/app/app.py"):
        mark_release_loaded(data_dir, app_path)
    os.chdir(str(app_path.parent))
    os.execv(sys.executable, [sys.executable, str(app_path)])


if __name__ == "__main__":
    main()
