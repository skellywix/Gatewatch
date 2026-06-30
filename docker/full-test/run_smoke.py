#!/usr/bin/env python3
"""Run the Gatewatch full-test proxy browser smoke through Docker Compose."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    "docker/full-test/.env.example",
    "-f",
    "docker/full-test/compose.yaml",
]


def run(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def output(command: list[str], *, echo: bool = False) -> str:
    if echo:
        print("$ " + " ".join(command), flush=True)
    return subprocess.check_output(command, cwd=REPO_ROOT, text=True).strip()


def wait_for_service(service: str, timeout_seconds: int = 120) -> None:
    print(f"Waiting for {service} to become healthy...", flush=True)
    deadline = time.time() + timeout_seconds
    last_status = "not created"
    while time.time() < deadline:
        container_id = output([*COMPOSE, "ps", "-q", service])
        if container_id:
            status = output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    container_id,
                ]
            )
            last_status = status
            if status in {"healthy", "running"}:
                return
            if status in {"exited", "dead"}:
                raise RuntimeError(f"{service} exited while waiting for health")
        time.sleep(2)
    raise TimeoutError(f"{service} did not become healthy within {timeout_seconds}s; last status: {last_status}")


def cleanup() -> None:
    subprocess.run([*COMPOSE, "down", "-v", "--remove-orphans"], cwd=REPO_ROOT, check=False)


def main() -> int:
    try:
        cleanup()
        run([*COMPOSE, "up", "-d", "--build", "app"])
        wait_for_service("app")
        run([*COMPOSE, "up", "-d", "proxy"])
        wait_for_service("proxy")
        run([*COMPOSE, "run", "--rm", "browser-smoke"])
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
