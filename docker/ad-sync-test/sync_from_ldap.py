#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


LDAP_ATTRIBUTES = [
    "employeeID",
    "displayName",
    "cn",
    "mail",
    "department",
    "physicalDeliveryOfficeName",
    "manager",
    "userAccountControl",
    "objectGUID",
    "userPrincipalName",
    "sAMAccountName",
    "distinguishedName",
    "lastLogonTimestamp",
]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def run_ldapsearch() -> str:
    uri = env("AD_LDAP_URI", "ldaps://ad:636")
    bind_dn = env("AD_BIND_DN", "svc.gatewatch.adsync@GATEWATCH.TEST")
    password = require_env("AD_BIND_PASSWORD")
    base_dn = env("AD_BASE_DN", "DC=gatewatch,DC=test")
    ldap_filter = env("AD_LDAP_FILTER", "(&(objectClass=user)(employeeID=*))")
    command = [
        "ldapsearch",
        "-LLL",
        "-o",
        "ldif-wrap=no",
        "-H",
        uri,
        "-D",
        bind_dn,
        "-w",
        password,
        "-b",
        base_dn,
        ldap_filter,
        *LDAP_ATTRIBUTES,
    ]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "LDAPTLS_REQCERT": env("LDAPTLS_REQCERT", "never")},
    )
    if result.returncode != 0:
        raise RuntimeError(f"ldapsearch failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def unfold_ldif(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_ldif(text: str) -> list[dict[str, list[bytes]]]:
    records: list[dict[str, list[bytes]]] = []
    current: dict[str, list[bytes]] = {}
    for line in unfold_ldif(text):
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        if line.startswith("#"):
            continue
        if "::" in line:
            key, value = line.split("::", 1)
            decoded = base64.b64decode(value.strip())
        elif ":" in line:
            key, value = line.split(":", 1)
            decoded = value.lstrip().encode("utf-8")
        else:
            continue
        current.setdefault(key, []).append(decoded)
    if current:
        records.append(current)
    return records


def first(record: dict[str, list[bytes]], key: str) -> str:
    values = record.get(key) or []
    if not values:
        return ""
    try:
        return values[0].decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(values[0]).decode("ascii")


def object_guid(record: dict[str, list[bytes]]) -> str:
    values = record.get("objectGUID") or []
    if not values:
        return ""
    raw = values[0]
    if len(raw) == 16:
        return str(uuid.UUID(bytes_le=raw))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii")


def enabled_from_uac(value: str) -> str:
    try:
        flags = int(value)
    except (TypeError, ValueError):
        return "TRUE"
    return "FALSE" if flags & 2 else "TRUE"


def windows_filetime_to_iso(value: str) -> str:
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return ""
    if ticks <= 0:
        return ""
    unix_seconds = (ticks - 116444736000000000) / 10_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(unix_seconds))


def records_to_csv(records: list[dict[str, list[bytes]]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "EmployeeID",
            "Name",
            "Mail",
            "Department",
            "Office",
            "Manager",
            "Enabled",
            "ObjectGUID",
            "UserPrincipalName",
            "SamAccountName",
            "DistinguishedName",
            "LastLogonDate",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "EmployeeID": first(record, "employeeID"),
                "Name": first(record, "displayName") or first(record, "cn"),
                "Mail": first(record, "mail"),
                "Department": first(record, "department"),
                "Office": first(record, "physicalDeliveryOfficeName"),
                "Manager": first(record, "manager"),
                "Enabled": enabled_from_uac(first(record, "userAccountControl")),
                "ObjectGUID": object_guid(record),
                "UserPrincipalName": first(record, "userPrincipalName"),
                "SamAccountName": first(record, "sAMAccountName"),
                "DistinguishedName": first(record, "distinguishedName") or first(record, "dn"),
                "LastLogonDate": windows_filetime_to_iso(first(record, "lastLogonTimestamp")),
            }
        )
    return output.getvalue()


def gatewatch_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Remote-User": env("GATEWATCH_SYNC_REMOTE_USER", "GATEWATCH\\svc.gatewatch.adsync"),
        "X-Remote-Name": env("GATEWATCH_SYNC_REMOTE_NAME", "Gatewatch AD Sync Service"),
        "X-Remote-Groups": env("GATEWATCH_SYNC_REMOTE_GROUPS", "GATEWATCH\\AccessRegister-Admins"),
    }
    email = env("GATEWATCH_SYNC_REMOTE_EMAIL", "svc.gatewatch.adsync@gatewatch.test")
    if email:
        headers["X-Remote-Email"] = email
    secret = require_env("GATEWATCH_PROXY_SECRET")
    headers["X-Access-Register-Proxy-Secret"] = secret
    return headers


def post_json(path: str, payload: dict) -> dict:
    base_url = env("GATEWATCH_URL", "http://app:8087").rstrip("/")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=gatewatch_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8")
        raise RuntimeError(f"Gatewatch POST {path} failed with HTTP {error.code}: {details}") from error


def wait_for_gatewatch() -> None:
    base_url = env("GATEWATCH_URL", "http://app:8087").rstrip("/")
    deadline = time.monotonic() + int(env("GATEWATCH_WAIT_SECONDS", "90"))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        request = urllib.request.Request(f"{base_url}/api/summary", headers=gatewatch_headers())
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
                return
        except Exception as error:  # noqa: BLE001 - this is a readiness loop.
            last_error = error
            time.sleep(2)
    raise RuntimeError(f"Gatewatch did not become ready: {last_error}")


def wait_for_ldap() -> str:
    deadline = time.monotonic() + int(env("AD_WAIT_SECONDS", "120"))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return run_ldapsearch()
        except Exception as error:  # noqa: BLE001 - this is a readiness loop.
            last_error = error
            time.sleep(3)
    raise RuntimeError(f"LDAP did not become ready: {last_error}")


def main() -> int:
    wait_for_gatewatch()
    ldif = wait_for_ldap()
    records = parse_ldif(ldif)
    if not records:
        raise RuntimeError("LDAP query returned no user records")
    directory_text = records_to_csv(records)
    sync_response = post_json(
        "/api/ad/sync",
        {
            "source_name": env("GATEWATCH_SYNC_SOURCE", "Docker LDAP service-account sync"),
            "format": "csv",
            "directory_text": directory_text,
        },
    )
    ad_sync_run = sync_response.get("adSyncRun") or {}
    if ad_sync_run.get("error_rows", 0):
        raise RuntimeError(f"Gatewatch reported AD sync row errors: {ad_sync_run}")

    route_response = None
    if env("GATEWATCH_ROUTE_DISABLED_ACCESS", "0").lower() in {"1", "true", "yes"}:
        route_response = post_json("/api/disabled-access/route-removal", {})

    result = {
        "ldap_rows": len(records),
        "adSyncRun": ad_sync_run,
        "routeResult": (route_response or {}).get("result"),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - command-line tool should report cleanly.
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
