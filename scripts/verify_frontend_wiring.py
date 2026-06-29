#!/usr/bin/env python3
"""Verify the browser UI renders database data and sees API updates."""

from __future__ import annotations

import json
import os
import base64
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import contextlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import STATIC_DIR, Store, make_handler  # noqa: E402


ADMIN_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-App-Role": "Admin",
    "X-App-Actor": "Frontend Wiring Check",
}


class WiringServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


class CdpConnection:
    def __init__(self, websocket_url: str):
        self.next_id = 0
        parsed = urlparse(websocket_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path
        if parsed.query:
            self.path = f"{self.path}?{parsed.query}"
        self.socket = socket.create_connection((self.host, self.port), timeout=2)
        self.socket.settimeout(10)
        self._handshake()

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=0x8)
        except OSError:
            pass
        self.socket.close()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.socket.recv(4096)
            if not chunk:
                break
            response += chunk
        if not response.startswith(b"HTTP/1.1 101"):
            raise SystemExit(f"DevTools WebSocket handshake failed:\n{response.decode('utf-8', errors='replace')}")

    def _read_exact(self, length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise SystemExit("DevTools WebSocket closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.socket.sendall(bytes(header) + masked)

    def _receive_json(self) -> dict:
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            masked = bool(second & 0x80)
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise SystemExit("DevTools WebSocket closed.")
            if opcode == 0x9:
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode != 0x1:
                continue
            return json.loads(payload.decode("utf-8"))

    def command(self, method: str, params: dict | None = None, session_id: str | None = None, timeout: float = 10) -> dict:
        self.next_id += 1
        command_id = self.next_id
        payload = {"id": command_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id
        self.socket.settimeout(timeout)
        self._send_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        while True:
            message = self._receive_json()
            if message.get("id") == command_id:
                if "error" in message:
                    raise SystemExit(f"DevTools command {method} failed: {message['error']}")
                return message


class BrowserController:
    def __init__(self, browser: str):
        self.browser = browser
        self.profile_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.port = self._free_port()
        self.process = subprocess.Popen(
            [
                self.browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--disable-background-networking",
                "--disable-dev-shm-usage",
                "--remote-debugging-address=127.0.0.1",
                "--remote-allow-origins=*",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile_dir.name}",
                "--window-size=1440,1100",
                "about:blank",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        try:
            websocket_url = self._wait_for_websocket_url()
            last_error: Exception | None = None
            for _attempt in range(20):
                try:
                    self.connection = CdpConnection(websocket_url)
                    break
                except (OSError, TimeoutError) as error:
                    last_error = error
                    time.sleep(0.25)
            else:
                raise SystemExit(f"DevTools WebSocket was advertised but not reachable: {last_error}") from last_error
        except Exception:
            self._stop_process()
            self._cleanup_profile()
            raise

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._stop_process()
            self._cleanup_profile()

    def _stop_process(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=8)

    def _cleanup_profile(self) -> None:
        for _attempt in range(8):
            try:
                self.profile_dir.cleanup()
                return
            except PermissionError:
                time.sleep(0.25)
        with contextlib.suppress(PermissionError):
            self.profile_dir.cleanup()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _wait_for_websocket_url(self) -> str:
        version_url = f"http://127.0.0.1:{self.port}/json/version"
        for _attempt in range(150):
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise SystemExit(f"Browser exited before DevTools was ready:\n{stderr}")
            try:
                with urllib.request.urlopen(version_url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    return payload["webSocketDebuggerUrl"].replace("ws://localhost:", "ws://127.0.0.1:")
            except Exception:
                time.sleep(0.1)
        raise SystemExit("Browser DevTools endpoint did not become ready.")

    def rendered_text(self, url: str, expected: list[str], timeout: float = 12) -> str:
        expression = "document.body ? document.body.innerText : ''"
        last_text = ""
        for _attempt in range(3):
            target = self.connection.command("Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
            session_id = self.connection.command(
                "Target.attachToTarget",
                {"targetId": target, "flatten": True},
            )["result"]["sessionId"]
            try:
                self.connection.command("Page.enable", session_id=session_id)
                self.connection.command("Runtime.enable", session_id=session_id)
                self.connection.command("Target.activateTarget", {"targetId": target})
                self.connection.command("Page.navigate", {"url": url}, session_id=session_id)
                deadline = time.time() + timeout
                text = ""
                while time.time() < deadline:
                    result = self.connection.command(
                        "Runtime.evaluate",
                        {"expression": expression, "returnByValue": True},
                        session_id=session_id,
                        timeout=timeout,
                    )["result"]["result"]
                    text = result.get("value") or ""
                    if all(item in text for item in expected):
                        return text
                    time.sleep(0.2)
                last_text = text
            finally:
                self.connection.command("Target.closeTarget", {"targetId": target})
        return last_text

    def set_local_storage(self, url: str, key: str, value: str) -> None:
        target = self.connection.command("Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
        session_id = self.connection.command(
            "Target.attachToTarget",
            {"targetId": target, "flatten": True},
        )["result"]["sessionId"]
        self.connection.command("Page.enable", session_id=session_id)
        self.connection.command("Runtime.enable", session_id=session_id)
        self.connection.command("Target.activateTarget", {"targetId": target})
        try:
            self.connection.command("Page.navigate", {"url": url}, session_id=session_id)
            deadline = time.time() + 8
            while time.time() < deadline:
                state = self.connection.command(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                    session_id=session_id,
                )["result"]["result"].get("value")
                if state in {"interactive", "complete"}:
                    break
                time.sleep(0.1)
            expression = f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})"
            self.connection.command("Runtime.evaluate", {"expression": expression}, session_id=session_id)
        finally:
            self.connection.command("Target.closeTarget", {"targetId": target})


def find_browser() -> str:
    candidates = [
        "chrome",
        "chrome.exe",
        "msedge",
        "msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if Path(candidate).exists():
            return candidate
    raise SystemExit("Chrome or Edge is required for frontend wiring verification.")


def request_json(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        method=method,
        headers=ADMIN_HEADERS,
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} failed with HTTP {error.code}: {detail}") from error


def wait_for_server(base_url: str) -> None:
    for _attempt in range(30):
        try:
            request_json(base_url, "GET", "/api/bootstrap")
            return
        except Exception:
            time.sleep(0.1)
    raise SystemExit("Frontend wiring server did not become ready.")


def assert_contains(text: str, expected: str, label: str) -> None:
    if expected not in text:
        raise SystemExit(f"Expected {label} to contain {expected!r}.")


def assert_not_contains(text: str, unexpected: str, label: str) -> None:
    if unexpected in text:
        raise SystemExit(f"Expected {label} not to contain {unexpected!r}.")


def main() -> int:
    browser = find_browser()
    controller = BrowserController(browser)
    with tempfile.TemporaryDirectory() as tempdir:
        store = Store(Path(tempdir) / "frontend-wiring.db")
        store.init(seed=True)
        handler = make_handler(store, STATIC_DIR)
        server = WiringServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            wait_for_server(base_url)

            home_text = controller.rendered_text(
                f"{base_url}/?view=dashboard",
                ["4 active access records", "Access Snapshot", "Avery Morgan"],
            )
            assert_contains(home_text, "4 active access records", "home dashboard")
            assert_contains(home_text, "Access Snapshot", "home dashboard")
            assert_contains(home_text, "Avery Morgan", "database-backed employee data")

            bootstrap = request_json(base_url, "GET", "/api/bootstrap")
            employee = next(item for item in bootstrap["employees"] if item["name"] == "Avery Morgan")
            updated_name = "Avery Morgan QA"
            request_json(base_url, "PATCH", f"/api/employees/{employee['id']}", {"name": updated_name})

            profile_text = controller.rendered_text(
                f"{base_url}/?view=profile&employee={employee['id']}",
                [updated_name, "Save profile to database", "Current Access"],
            )
            assert_contains(profile_text, updated_name, "employee profile")
            assert_contains(profile_text, "Save profile to database", "profile edit form")
            assert_contains(profile_text, "Current Access", "profile access panel")

            updated_bootstrap = request_json(base_url, "GET", "/api/bootstrap")
            audit_summaries = [item["summary"] for item in updated_bootstrap["audit"]]
            if f"Updated employee {updated_name}." not in audit_summaries:
                raise SystemExit("Employee PATCH did not create the expected audit entry.")

            controller.close()
            controller = BrowserController(browser)
            controller.set_local_storage(f"{base_url}/", "access-register-role", "User")
            user_text = controller.rendered_text(
                f"{base_url}/?view=configuration",
                ["Home", "Access Snapshot", "4 active access records"],
            )
            assert_contains(user_text, "Home", "User settings redirect")
            assert_contains(user_text, "Access Snapshot", "User settings redirect")
            assert_contains(user_text, "4 active access records", "User settings redirect")
            assert_not_contains(user_text, "Refresh settings", "User settings redirect")
            assert_not_contains(user_text, "Export audit CSV", "User settings redirect")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            controller.close()

    print("Frontend wiring verified: browser render, profile route, API PATCH, audit sync, and role visibility all passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
