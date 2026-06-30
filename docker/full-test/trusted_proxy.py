#!/usr/bin/env python3
"""Small authenticated reverse proxy for the Gatewatch Docker full-test lab."""

from __future__ import annotations

import http.client
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
STRIPPED_PREFIXES = (
    "x-remote-",
    "x-forwarded-user",
    "x-forwarded-email",
    "x-forwarded-groups",
    "x-authenticated-",
    "x-gatewatch-proxy-secret",
    "x-access-register-proxy-secret",
)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


TARGET = urlparse(env("GATEWATCH_PROXY_TARGET", "http://app:8087"))
TARGET_PORT = TARGET.port or (443 if TARGET.scheme == "https" else 80)
PROXY_SECRET = env("GATEWATCH_PROXY_SECRET")
REMOTE_USER = env("GATEWATCH_PROXY_USER", "GATEWATCH\\gw.admin")
REMOTE_NAME = env("GATEWATCH_PROXY_NAME", "Grace Admin")
REMOTE_EMAIL = env("GATEWATCH_PROXY_EMAIL", "grace.admin@gatewatch.test")
REMOTE_GROUPS = env("GATEWATCH_PROXY_GROUPS", "GATEWATCH\\Gatewatch-Admins")
REMOTE_TENANT = env("GATEWATCH_PROXY_TENANT", "gatewatch-full-test")


class TrustedProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GatewatchTestProxy/1.0"

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def do_OPTIONS(self) -> None:
        self._forward()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _forward(self) -> None:
        if not PROXY_SECRET:
            self._send_text(500, "Proxy secret is not configured")
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        headers = self._forward_headers()
        connection = http.client.HTTPConnection(TARGET.hostname, TARGET_PORT, timeout=15)
        try:
            path = self.path
            if TARGET.path and TARGET.path != "/":
                path = TARGET.path.rstrip("/") + self.path
            connection.request(self.command, path, body=body or None, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
        except OSError as error:
            self._send_text(502, f"Gatewatch upstream request failed: {error}")
            return
        finally:
            connection.close()

        self.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            lower = name.lower()
            if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                continue
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def _forward_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in HOP_BY_HOP_HEADERS or any(lower.startswith(prefix) for prefix in STRIPPED_PREFIXES):
                continue
            headers[name] = value

        original_host = self.headers.get("Host") or TARGET.netloc
        headers["Host"] = original_host
        headers["X-Forwarded-Host"] = original_host
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Gatewatch-Proxy-Secret"] = PROXY_SECRET
        headers["X-Remote-User"] = REMOTE_USER
        headers["X-Remote-Name"] = REMOTE_NAME
        headers["X-Remote-Email"] = REMOTE_EMAIL
        headers["X-Remote-Groups"] = REMOTE_GROUPS
        headers["X-Remote-Tenant"] = REMOTE_TENANT
        return headers

    def _send_text(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    if TARGET.scheme != "http" or not TARGET.hostname:
        raise SystemExit("GATEWATCH_PROXY_TARGET must be an http URL")
    host = env("GATEWATCH_PROXY_HOST", "0.0.0.0")
    port = int(env("GATEWATCH_PROXY_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), TrustedProxyHandler)
    print(f"Gatewatch test proxy listening on http://{host}:{port} -> {TARGET.geturl()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
