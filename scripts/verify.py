#!/usr/bin/env python3
"""Run Gatewatch's local verification checklist."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    description: str
    requires: str | None = None
    optional: bool = False
    display_command: list[str] | None = None


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the same compile, test, and syntax checks used for Gatewatch changes."
    )
    parser.add_argument(
        "--repeat",
        type=positive_int,
        default=1,
        help="Run the checklist multiple times to catch intermittent failures.",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Also build the production Docker image after the code checks pass.",
    )
    parser.add_argument(
        "--docker-full-test",
        action="store_true",
        help="Also run the docker/full-test trusted-proxy browser SSO smoke.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the selected checklist without running it.",
    )
    return parser.parse_args(argv)


def checks(include_docker: bool, include_docker_full_test: bool = False) -> list[Check]:
    selected = [
        Check(
            "Python compile",
            [sys.executable, "-m", "compileall", "-q", "app.py", "scripts", "tests", "docker/full-test", "deploy/mock-local"],
            "Compile all Python source, tests, Docker full-test helpers, and mock deployment helpers without executing the app.",
            display_command=[
                "python",
                "-m",
                "compileall",
                "-q",
                "app.py",
                "scripts",
                "tests",
                "docker/full-test",
                "deploy/mock-local",
            ],
        ),
        Check(
            "Backend and UI smoke tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            "Run backend lifecycle tests, HTTP UI workflow smoke tests, updater tests, and the 1000-scenario generated matrix.",
            display_command=["python", "-m", "unittest", "discover", "-s", "tests"],
        ),
        Check(
            "Frontend JavaScript syntax",
            ["node", "--check", "web/app.js"],
            "Parse the browser app JavaScript before it reaches the UI when Node is available.",
            requires="node",
            optional=True,
        ),
        Check(
            "Theme bootstrap JavaScript syntax",
            ["node", "--check", "web/theme.js"],
            "Parse the theme bootstrap that runs before CSS so the CSP-safe first paint path stays valid.",
            requires="node",
            optional=True,
        ),
        Check(
            "Frontend monitor regression",
            ["node", "--test", "tests/frontend-monitor.test.js"],
            "Exercise the default monitor tab plus overview search, filters, and selection states when Node is available.",
            requires="node",
            optional=True,
        ),
        Check(
            "Mock deployment package inspection",
            [sys.executable, "deploy/mock-local/mock_deploy.py", "inspect-package"],
            "Validate the reusable mock deployment package, manifest, and checklist guide.",
            display_command=["python", "deploy/mock-local/mock_deploy.py", "inspect-package"],
        ),
    ]
    if include_docker:
        selected.append(
            Check(
                "Production Docker build",
                ["docker", "build", "-t", "gatewatch-ci", "."],
                "Build the production image using the checked-in Dockerfile.",
                requires="docker",
            )
        )
    if include_docker_full_test:
        selected.extend(
            [
                Check(
                    "Full-test proxy Compose config",
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        "docker/full-test/.env.example",
                        "-f",
                        "docker/full-test/compose.yaml",
                        "config",
                        "--quiet",
                    ],
                    "Validate the trusted-proxy full-test Compose file.",
                    requires="docker",
                ),
                Check(
                    "Full-test browser SSO smoke",
                    [sys.executable, "docker/full-test/run_smoke.py"],
                    "Start the app behind the test proxy and prove browser SSO role mapping through the proxy.",
                    requires="docker",
                    display_command=["python", "docker/full-test/run_smoke.py"],
                ),
            ]
        )
    return selected


def ensure_executables(selected: list[Check]) -> None:
    missing = sorted(
        {
            check.requires
            for check in selected
            if check.requires and not check.optional and shutil.which(check.requires) is None
        }
    )
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"Missing required executable: {names}")


def is_available(check: Check) -> bool:
    return check.requires is None or shutil.which(check.requires) is not None


def runnable_checks(selected: list[Check]) -> list[Check]:
    return [check for check in selected if is_available(check)]


def format_command(command: list[str]) -> str:
    return " ".join(command)


def shown_command(check: Check) -> list[str]:
    return check.display_command or check.command


def skipped_checks(
    include_docker: bool,
    include_docker_full_test: bool = False,
    selected: list[Check] | None = None,
) -> list[str]:
    skipped = []
    for check in selected or []:
        if check.optional and not is_available(check):
            skipped.append(f"{check.name} ({check.requires} not installed)")
    if not include_docker:
        skipped.append("Production Docker build (use --docker)")
    if not include_docker_full_test:
        skipped.append("Full-test browser SSO smoke (use --docker-full-test)")
    return skipped


def print_skipped_checks(skipped: list[str]) -> None:
    if not skipped:
        return
    print("Skipped optional check(s):")
    for item in skipped:
        print(f"- {item}")


def print_checklist(selected: list[Check], repeat: int, skipped: list[str] | None = None) -> None:
    print(f"Gatewatch verification checklist ({len(selected)} check(s) x {repeat} run(s))")
    for index, check in enumerate(selected, start=1):
        print(f"{index}. {check.name}")
        print(f"   {check.description}")
        print(f"   $ {format_command(shown_command(check))}")
    print_skipped_checks(skipped or [])


def run_check(check: Check, index: int, total: int, cycle: int, repeat: int) -> None:
    label = f"[{cycle}/{repeat} {index}/{total}] {check.name}"
    print(f"\n{label}", flush=True)
    print(f"$ {format_command(shown_command(check))}", flush=True)
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            check.command,
            cwd=REPO_ROOT,
            stderr=subprocess.STDOUT,
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
        )
    except OSError as error:
        raise SystemExit(f"{check.name} could not start: {error}") from error
    if process.stdout is None:
        raise SystemExit(f"{check.name} failed to expose command output")
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
    finally:
        process.stdout.close()
    returncode = process.wait()
    elapsed = time.perf_counter() - started
    if returncode:
        raise SystemExit(f"{check.name} failed with exit code {returncode} after {elapsed:.1f}s")
    print(f"{check.name} passed in {elapsed:.1f}s", flush=True)


def main() -> int:
    args = parse_args()
    selected = checks(include_docker=args.docker, include_docker_full_test=args.docker_full_test)
    skipped = skipped_checks(
        include_docker=args.docker,
        include_docker_full_test=args.docker_full_test,
        selected=selected,
    )
    if args.list:
        print_checklist(selected, args.repeat, skipped)
        return 0
    ensure_executables(selected)
    selected = runnable_checks(selected)

    started = time.perf_counter()
    for cycle in range(1, args.repeat + 1):
        for index, check in enumerate(selected, start=1):
            run_check(check, index, len(selected), cycle, args.repeat)

    elapsed = time.perf_counter() - started
    print(f"\nGatewatch verification passed: {len(selected)} check(s) x {args.repeat} run(s) in {elapsed:.1f}s")
    print_skipped_checks(skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
