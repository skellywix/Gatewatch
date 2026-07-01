#!/usr/bin/env python3
"""Browser-style SSO smoke for the Gatewatch full-test proxy."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


BASE_URL = os.environ.get("GATEWATCH_BROWSER_SMOKE_URL", "http://proxy:8080").rstrip("/")
EXPECTED_EMAIL = os.environ.get("GATEWATCH_BROWSER_SMOKE_EXPECTED_EMAIL", "").strip()
EXPECTED_ADMIN = os.environ.get("GATEWATCH_BROWSER_SMOKE_EXPECTED_ADMIN", "1").strip() in {"1", "true", "yes"}
CSRF_TOKEN = ""


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json,text/html",
        "User-Agent": "GatewatchBrowserSmoke/1.0",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if method in {"POST", "PATCH", "DELETE"}:
        parsed = urlparse(BASE_URL)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        if CSRF_TOKEN:
            headers["X-Gatewatch-CSRF"] = CSRF_TOKEN
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read().decode("utf-8")
        if response.headers.get_content_type() == "application/json":
            return response.status, json.loads(body)
        return response.status, body


def wait_for_proxy() -> None:
    deadline = time.time() + 60
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            request("GET", "/healthz")
            return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(1)
    raise AssertionError(f"proxy did not become ready: {last_error}")


def main() -> int:
    global CSRF_TOKEN
    wait_for_proxy()

    status, html = request("GET", "/")
    assert status == 200, status
    assert isinstance(html, str) and "Gatewatch" in html and "Employee Access Tracker" in html

    _, bootstrap = request("GET", "/api/bootstrap")
    auth = bootstrap["auth"]
    user = auth["user"]
    permissions = auth["permissions"]
    CSRF_TOKEN = auth.get("csrfToken", "")
    assert auth["provider"] == "trusted_proxy", auth
    assert CSRF_TOKEN, auth
    if EXPECTED_EMAIL:
        assert user["email"] == EXPECTED_EMAIL, user
    assert bool(permissions["canModifyEmployees"]) is EXPECTED_ADMIN, permissions

    stamp = str(int(time.time()))
    _, created = request(
        "POST",
        "/api/employees",
        {
            "employee_id": f"SSO-SMOKE-{stamp}",
            "name": "Browser SSO Smoke",
            "email": f"browser.sso.smoke.{stamp}@gatewatch.test",
            "request_source": "Trusted proxy browser smoke",
            "access_needed": "End-to-end role mapping proof",
        },
    )
    employee_id = created["employee"]["id"]
    _, deleted = request("DELETE", f"/api/employees/{employee_id}")
    assert deleted["employee"]["name"] == "Browser SSO Smoke", deleted

    _, audit = request("GET", "/api/audit-log")
    assert audit["audit"][0]["actor"] == user["actor"], audit["audit"][0]

    print("Browser SSO smoke passed")
    print(f"- url: {BASE_URL}")
    print(f"- user: {user['actor']}")
    print(f"- canModifyEmployees: {permissions['canModifyEmployees']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
