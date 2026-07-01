#!/usr/bin/env python3
"""Start Gatewatch from the current persistent release when one is staged."""

from __future__ import annotations

import os
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


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
    app_path = release_dir / "app.py"
    return app_path if app_path.is_file() else None


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
