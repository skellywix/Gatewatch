from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import mimetypes
import os
import sqlite3
import sys
import secrets
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
DEFAULT_DB_PATH = BASE_DIR / "data" / "gatewatch.db"
MAX_JSON_BODY_BYTES = 1_000_000
EMPLOYEE_STATUSES = {"active", "disabled", "terminated"}
SESSION_COOKIE = "gatewatch_session"
OAUTH_COOKIE = "gatewatch_oauth"
SESSION_SECRET = os.environ.get("GATEWATCH_SESSION_SECRET") or secrets.token_urlsafe(48)
ENTRA_SIGNIN_SCOPES = "openid profile email offline_access User.Read"
ENTRA_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_ADMIN_GROUP_CANONICAL = "gcefcu.org/Users/Domain Admins"
ENTRA_GRAPH_SELECT = ",".join(
    [
        "id",
        "displayName",
        "mail",
        "userPrincipalName",
        "department",
        "jobTitle",
        "officeLocation",
        "accountEnabled",
        "employeeId",
    ]
)
ENTRA_GROUP_SELECT = ",".join(
    [
        "id",
        "displayName",
        "mailNickname",
        "onPremisesSamAccountName",
        "onPremisesSecurityIdentifier",
    ]
)
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
CHECKLIST_FIELDS = (
    "request_received",
    "manager_approved",
    "it_provisioned",
    "employee_notified",
)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [row_to_dict(row) for row in rows]


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalize_text(value, field_label: str, *, required: bool = False, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ApiError(400, f"{field_label} is required")
    if len(text) > maximum:
        raise ApiError(400, f"{field_label} must be {maximum} characters or fewer")
    if "\x00" in text:
        raise ApiError(400, f"{field_label} cannot contain null characters")
    return text


def normalize_email(value, *, required: bool = False) -> str:
    email = normalize_text(value, "Email", required=required, maximum=254).lower()
    if not email:
        return ""
    if email.count("@") != 1 or any(char.isspace() for char in email):
        raise ApiError(400, "Email must be a plain email address")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise ApiError(400, "Email must be a plain email address")
    return email


def normalize_status(value) -> str:
    status = str(value or "active").strip().lower()
    if status not in EMPLOYEE_STATUSES:
        raise ApiError(400, "Status must be active, disabled, or terminated")
    return status


def normalize_bool_int(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if value in (1, "1"):
        return 1
    if value in (0, "0", None, ""):
        return 0
    text = str(value).strip().lower()
    if text in {"true", "yes", "on"}:
        return 1
    if text in {"false", "no", "off"}:
        return 0
    raise ApiError(400, "Checklist values must be true or false")


def csv_safe_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def is_loopback_bind(host: str | None) -> bool:
    text = str(host or "").strip().lower()
    if text == "localhost":
        return True
    if not text:
        return False
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def allow_insecure_network() -> bool:
    value = os.environ.get("GATEWATCH_ALLOW_INSECURE_NETWORK")
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_startup_security(host: str | None) -> None:
    if is_loopback_bind(host):
        return
    if allow_insecure_network():
        sys.stderr.write(
            "WARNING: Gatewatch is listening on a non-loopback address without built-in authentication.\n"
        )
        return
    raise RuntimeError(
        "Refusing to expose Gatewatch on a non-loopback address without explicit approval. "
        "Keep GATEWATCH_HOST=127.0.0.1, put a reverse proxy in front of it, or set "
        "GATEWATCH_ALLOW_INSECURE_NETWORK=1 only for an isolated internal demo."
    )


def base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def signed_payload(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{base64url_encode(raw)}.{base64url_encode(signature)}"


def unsign_payload(value: str | None) -> dict | None:
    if not value or "." not in value:
        return None
    raw_text, signature_text = value.split(".", 1)
    try:
        raw = base64url_decode(raw_text)
        signature = base64url_decode(signature_text)
    except Exception:
        return None
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    expires_at = payload.get("exp")
    if expires_at is not None:
        try:
            if float(expires_at) < time.time():
                return None
        except (TypeError, ValueError):
            return None
    return payload


def parse_cookies(header: str | None) -> dict[str, str]:
    cookies = {}
    for item in str(header or "").split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def make_cookie(
    name: str,
    value: str,
    *,
    max_age: int = 0,
    path: str = "/",
    secure: bool = False,
) -> str:
    parts = [f"{name}={value}", f"Path={path}", "HttpOnly", "SameSite=Lax"]
    if max_age:
        parts.append(f"Max-Age={max_age}")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(name: str, *, path: str = "/") -> str:
    return f"{name}=; Path={path}; HttpOnly; SameSite=Lax; Max-Age=0"


def entra_authority_path(path: str, tenant_id: str | None = None) -> str:
    tenant = tenant_id or os.environ.get("GATEWATCH_ENTRA_TENANT_ID", "")
    tenant = str(tenant or "").strip()
    if not tenant:
        raise ApiError(503, "Microsoft Entra ID is not configured")
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{path.lstrip('/')}"


def configured_redirect_uri() -> str:
    explicit = os.environ.get("GATEWATCH_ENTRA_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    host = os.environ.get("GATEWATCH_HOST", "127.0.0.1")
    port = os.environ.get("GATEWATCH_PORT", "8087")
    if is_loopback_bind(host):
        display_host = "127.0.0.1" if host not in {"localhost", "::1"} else host
        return f"http://{display_host}:{port}/auth/entra/callback"
    return ""


def entra_config() -> dict:
    tenant_id = os.environ.get("GATEWATCH_ENTRA_TENANT_ID", "").strip()
    client_id = os.environ.get("GATEWATCH_ENTRA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GATEWATCH_ENTRA_CLIENT_SECRET", "").strip()
    redirect_uri = configured_redirect_uri()
    graph_ready = all([tenant_id, client_id, client_secret])
    sso_ready = graph_ready and bool(redirect_uri)
    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "configured": graph_ready or sso_ready,
        "graph_configured": graph_ready,
        "sso_configured": sso_ready,
        "session_persistent": bool(os.environ.get("GATEWATCH_SESSION_SECRET", "").strip()),
    }


def admin_group_canonical() -> str:
    configured = os.environ.get("GATEWATCH_ADMIN_GROUP_CANONICAL", "").strip()
    return configured or DEFAULT_ADMIN_GROUP_CANONICAL


def admin_group_leaf() -> str:
    return admin_group_canonical().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()


def normalize_group_identifier(value) -> str:
    text = str(value or "").replace("\\", "/").strip().casefold()
    return " ".join(text.split())


def admin_group_identifiers() -> set[str]:
    canonical = admin_group_canonical()
    leaf = admin_group_leaf()
    return {
        normalize_group_identifier(item)
        for item in [canonical, leaf]
        if normalize_group_identifier(item)
    }


def group_matches_admin(group: dict) -> bool:
    expected = admin_group_identifiers()
    candidates = [
        group.get("id"),
        group.get("displayName"),
        group.get("mailNickname"),
        group.get("onPremisesSamAccountName"),
        group.get("onPremisesSecurityIdentifier"),
    ]
    return any(normalize_group_identifier(candidate) in expected for candidate in candidates)


def session_actor(session: dict | None) -> str:
    if not session:
        return "Local user"
    name = str(session.get("name") or "").strip()
    email = str(session.get("email") or "").strip()
    if name and email and name.casefold() != email.casefold():
        return f"{name} ({email})"
    return email or name or "Entra user"


def current_session(headers) -> dict | None:
    cookies = parse_cookies(headers.get("Cookie"))
    session = unsign_payload(cookies.get(SESSION_COOKIE))
    if not session:
        return None
    current = {
        "name": session.get("name") or session.get("email") or "Entra user",
        "email": session.get("email") or "",
        "tenant_id": session.get("tid") or "",
        "can_modify_employees": bool(session.get("can_modify_employees")),
        "admin_group": session.get("admin_group") or admin_group_canonical(),
        "group_check_error": session.get("group_check_error") or "",
        "groups_checked_at": session.get("groups_checked_at") or "",
    }
    current["actor"] = session_actor(current)
    return current


def auth_permissions_payload(headers) -> dict:
    session = current_session(headers)
    can_modify = bool(session and session.get("can_modify_employees"))
    if can_modify:
        reason = f"Signed in user is a member of {admin_group_canonical()}."
    elif session and session.get("group_check_error"):
        reason = "Group membership could not be verified; employee changes, sync, and configuration are locked."
    elif session:
        reason = f"Only members of {admin_group_canonical()} can edit, delete, sync, or view configuration."
    else:
        reason = f"Sign in as a member of {admin_group_canonical()} to edit, delete, sync, or view configuration."
    return {
        "canModifyEmployees": can_modify,
        "adminGroup": admin_group_canonical(),
        "actor": session_actor(session),
        "reason": reason,
    }


def auth_status_payload(headers) -> dict:
    config = entra_config()
    return {
        "entra": {
            "configured": config["configured"],
            "ssoConfigured": config["sso_configured"],
            "graphConfigured": config["graph_configured"],
            "sessionPersistent": config["session_persistent"],
            "redirectUri": config["redirect_uri"] if config["sso_configured"] else "",
            "loginUrl": "/auth/entra/login" if config["sso_configured"] else "",
            "logoutUrl": "/auth/logout",
            "syncUrl": "/api/entra/sync" if config["graph_configured"] else "",
            "user": current_session(headers),
            "permissions": auth_permissions_payload(headers),
        }
    }


def port_check(port_value) -> dict:
    text = str(port_value or "").strip()
    try:
        port = int(text)
    except ValueError:
        return {
            "key": "port",
            "label": "Port",
            "status": "blocked",
            "blocked": True,
            "message": "Port must be a number between 1 and 65535.",
        }
    if port < 1 or port > 65535:
        return {
            "key": "port",
            "label": "Port",
            "status": "blocked",
            "blocked": True,
            "message": "Port must be between 1 and 65535.",
        }
    return {
        "key": "port",
        "label": "Port",
        "status": "ok",
        "blocked": False,
        "message": f"Port {port} is valid. Use the OS firewall or reverse proxy checks to confirm it is reachable.",
    }


def network_binding_check(host: str, *, allow_insecure: bool | None = None) -> dict:
    allowed = allow_insecure_network() if allow_insecure is None else allow_insecure
    if is_loopback_bind(host):
        return {
            "key": "network",
            "label": "Network binding",
            "status": "ok",
            "blocked": False,
            "message": "Loopback binding is allowed and keeps unauthenticated HTTP local to this machine.",
        }
    if allowed:
        return {
            "key": "network",
            "label": "Network binding",
            "status": "warning",
            "blocked": False,
            "message": "Non-loopback binding is explicitly allowed. Put Microsoft SSO or a trusted reverse proxy in front of it.",
        }
    return {
        "key": "network",
        "label": "Network binding",
        "status": "blocked",
        "blocked": True,
        "message": "Gatewatch will refuse this host unless it stays loopback or GATEWATCH_ALLOW_INSECURE_NETWORK=1 is set for an isolated internal deployment.",
    }


def database_path_check(db_path: str) -> dict:
    if not db_path:
        return {
            "key": "database",
            "label": "Database",
            "status": "blocked",
            "blocked": True,
            "message": "A SQLite database path is required.",
        }
    parent = Path(db_path).expanduser().parent
    if parent.exists():
        return {
            "key": "database",
            "label": "Database",
            "status": "ok",
            "blocked": False,
            "message": "Database directory exists.",
        }
    return {
        "key": "database",
        "label": "Database",
        "status": "warning",
        "blocked": False,
        "message": "Database directory does not exist yet. The service account must be able to create or write it.",
    }


def microsoft_config_checks(
    *,
    tenant_id: str,
    client_id: str,
    client_secret_configured: bool,
    redirect_uri: str,
    admin_group: str,
) -> list[dict]:
    graph_missing = [
        label
        for label, present in [
            ("tenant ID", bool(tenant_id)),
            ("client ID", bool(client_id)),
            ("client secret", client_secret_configured),
        ]
        if not present
    ]
    sso_missing = [*graph_missing]
    if not redirect_uri:
        sso_missing.append("redirect URI")
    checks = []
    checks.append(
        {
            "key": "graph",
            "label": "Microsoft Graph",
            "status": "ok" if not graph_missing else "warning",
            "blocked": False,
            "message": "Directory sync can request Microsoft Graph tokens."
            if not graph_missing
            else f"Directory sync is missing {', '.join(graph_missing)}.",
        }
    )
    checks.append(
        {
            "key": "sso",
            "label": "Microsoft SSO",
            "status": "ok" if not sso_missing else "warning",
            "blocked": False,
            "message": "Microsoft sign-in is configured."
            if not sso_missing
            else f"Microsoft sign-in is missing {', '.join(sso_missing)}.",
        }
    )
    checks.append(
        {
            "key": "adminGroup",
            "label": "Domain Admin gate",
            "status": "ok" if admin_group else "blocked",
            "blocked": not bool(admin_group),
            "message": f"Employee edits, deletes, sync, and configuration require {admin_group}."
            if admin_group
            else "Configure the AD group that can administer Gatewatch.",
        }
    )
    return checks


def env_template_line(name: str, value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return f"{name}={text}"


def secret_placeholder(env_name: str, *, provided: bool = False) -> str:
    if provided:
        return "<provided in form>"
    if os.environ.get(env_name, "").strip():
        return "<already set on server>"
    return "<paste value here>"


def build_env_template(config: dict) -> str:
    lines = [
        env_template_line("GATEWATCH_HOST", config["host"]),
        env_template_line("GATEWATCH_PORT", config["port"]),
        env_template_line("GATEWATCH_DB", config["database_path"]),
        env_template_line("GATEWATCH_SESSION_SECRET", config["session_secret"]),
        env_template_line("GATEWATCH_ENTRA_TENANT_ID", config["tenant_id"]),
        env_template_line("GATEWATCH_ENTRA_CLIENT_ID", config["client_id"]),
        env_template_line("GATEWATCH_ENTRA_CLIENT_SECRET", config["client_secret"]),
        env_template_line("GATEWATCH_ENTRA_REDIRECT_URI", config["redirect_uri"]),
        env_template_line("GATEWATCH_ADMIN_GROUP_CANONICAL", config["admin_group"]),
    ]
    if config["allow_insecure_network"]:
        lines.append(env_template_line("GATEWATCH_ALLOW_INSECURE_NETWORK", "1"))
    return "\n".join(lines)


def config_checks(config: dict) -> list[dict]:
    checks = [
        network_binding_check(config["host"], allow_insecure=config["allow_insecure_network"]),
        port_check(config["port"]),
        database_path_check(config["database_path"]),
    ]
    checks.extend(
        microsoft_config_checks(
            tenant_id=config["tenant_id"],
            client_id=config["client_id"],
            client_secret_configured=config["client_secret_configured"],
            redirect_uri=config["redirect_uri"],
            admin_group=config["admin_group"],
        )
    )
    return checks


def admin_config_payload() -> dict:
    env_config = entra_config()
    host = os.environ.get("GATEWATCH_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("GATEWATCH_PORT", "8087").strip() or "8087"
    database_path = os.environ.get("GATEWATCH_DB", str(DEFAULT_DB_PATH)).strip() or str(DEFAULT_DB_PATH)
    admin_group = admin_group_canonical()
    config = {
        "host": host,
        "port": port,
        "database_path": database_path,
        "tenant_id": env_config["tenant_id"],
        "client_id": env_config["client_id"],
        "client_secret": secret_placeholder("GATEWATCH_ENTRA_CLIENT_SECRET"),
        "client_secret_configured": bool(env_config["client_secret"]),
        "redirect_uri": env_config["redirect_uri"],
        "admin_group": admin_group,
        "session_secret": secret_placeholder("GATEWATCH_SESSION_SECRET"),
        "session_secret_configured": env_config["session_persistent"],
        "allow_insecure_network": allow_insecure_network(),
    }
    return {
        "runtime": {
            "host": host,
            "port": port,
            "databasePath": database_path,
            "adminGroupCanonical": admin_group,
            "tenantId": env_config["tenant_id"],
            "clientId": env_config["client_id"],
            "redirectUri": env_config["redirect_uri"],
            "allowInsecureNetwork": allow_insecure_network(),
        },
        "secrets": {
            "sessionSecret": {
                "configured": env_config["session_persistent"],
                "message": "Persistent session secret is configured."
                if env_config["session_persistent"]
                else "Session secret is generated at startup; Microsoft sign-in cookies reset after restart.",
            },
            "entraClientSecret": {
                "configured": bool(env_config["client_secret"]),
                "message": "Microsoft Entra client secret is configured."
                if env_config["client_secret"]
                else "Microsoft Entra client secret is missing.",
            },
        },
        "checks": config_checks(config),
        "envTemplate": build_env_template(config),
    }


def admin_config_preview(payload: dict) -> dict:
    host = normalize_text(payload.get("host") or "127.0.0.1", "Host", maximum=120)
    port = normalize_text(payload.get("port") or "8087", "Port", maximum=10)
    database_path = normalize_text(payload.get("databasePath") or str(DEFAULT_DB_PATH), "Database path", maximum=500)
    tenant_id = normalize_text(payload.get("tenantId"), "Tenant ID", maximum=160)
    client_id = normalize_text(payload.get("clientId"), "Client ID", maximum=160)
    redirect_uri = normalize_text(payload.get("redirectUri"), "Redirect URI", maximum=300)
    admin_group = normalize_text(
        payload.get("adminGroupCanonical") or DEFAULT_ADMIN_GROUP_CANONICAL,
        "Domain Admin group",
        maximum=240,
    )
    client_secret_provided = bool(str(payload.get("clientSecret") or "").strip())
    session_secret_provided = bool(str(payload.get("sessionSecret") or "").strip())
    client_secret_configured = client_secret_provided or bool(os.environ.get("GATEWATCH_ENTRA_CLIENT_SECRET", "").strip())
    session_secret_configured = session_secret_provided or bool(os.environ.get("GATEWATCH_SESSION_SECRET", "").strip())
    config = {
        "host": host,
        "port": port,
        "database_path": database_path,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": secret_placeholder("GATEWATCH_ENTRA_CLIENT_SECRET", provided=client_secret_provided),
        "client_secret_configured": client_secret_configured,
        "redirect_uri": redirect_uri,
        "admin_group": admin_group,
        "session_secret": secret_placeholder("GATEWATCH_SESSION_SECRET", provided=session_secret_provided),
        "session_secret_configured": session_secret_configured,
        "allow_insecure_network": bool(payload.get("allowInsecureNetwork")),
    }
    return {
        "checks": config_checks(config),
        "envTemplate": build_env_template(config),
        "secrets": {
            "sessionSecret": {"configured": session_secret_configured},
            "entraClientSecret": {"configured": client_secret_configured},
        },
    }


def http_post_form(url: str, data: dict, timeout: int = 15) -> dict:
    encoded = urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:500]
        raise ApiError(502, f"Microsoft Entra ID request failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(502, f"Microsoft Entra ID request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(502, "Microsoft Entra ID returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ApiError(502, "Microsoft Entra ID returned invalid JSON")
    return parsed


def http_get_json(url: str, headers: dict, timeout: int = 15) -> dict:
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.netloc.lower() != "graph.microsoft.com":
        raise ApiError(502, "Microsoft Graph returned an unsafe pagination URL")
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:500]
        raise ApiError(502, f"Microsoft Graph request failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(502, f"Microsoft Graph request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(502, "Microsoft Graph returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ApiError(502, "Microsoft Graph returned invalid JSON")
    return parsed


def fetch_graph_me(access_token: str) -> dict:
    query = urlencode({"$select": "id,displayName,mail,userPrincipalName"})
    payload = http_get_json(
        f"https://graph.microsoft.com/v1.0/me?{query}",
        {"Authorization": f"Bearer {access_token}"},
    )
    if not payload.get("id"):
        raise ApiError(502, "Microsoft Graph did not return the signed-in user")
    return payload


def fetch_graph_me_groups(access_token: str) -> list[dict]:
    query = urlencode({"$select": ENTRA_GROUP_SELECT, "$top": "999"})
    url = f"https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.group?{query}"
    groups: list[dict] = []
    max_pages = int(os.environ.get("GATEWATCH_ENTRA_MAX_GROUP_PAGES", "10"))
    for _ in range(max_pages):
        payload = http_get_json(
            url,
            {
                "Authorization": f"Bearer {access_token}",
                "ConsistencyLevel": "eventual",
            },
        )
        page = payload.get("value", [])
        if not isinstance(page, list):
            raise ApiError(502, "Microsoft Graph returned invalid group membership payload")
        groups.extend([item for item in page if isinstance(item, dict)])
        next_link = payload.get("@odata.nextLink")
        if not next_link:
            return groups
        url = str(next_link)
    raise ApiError(502, "Microsoft Graph group membership payload exceeded the configured page limit")


def resolve_session_authorization(access_token: str) -> dict:
    checked_at = utc_now()
    try:
        groups = fetch_graph_me_groups(access_token)
    except ApiError as exc:
        return {
            "can_modify_employees": False,
            "admin_group": admin_group_canonical(),
            "groups_checked_at": checked_at,
            "group_check_error": exc.message,
        }
    return {
        "can_modify_employees": any(group_matches_admin(group) for group in groups),
        "admin_group": admin_group_canonical(),
        "groups_checked_at": checked_at,
        "group_check_error": "",
    }


def fetch_graph_users() -> list[dict]:
    config = entra_config()
    if not config["graph_configured"]:
        raise ApiError(503, "Microsoft Entra ID Graph sync is not configured")
    token = http_post_form(
        entra_authority_path("token", config["tenant_id"]),
        {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "grant_type": "client_credentials",
            "scope": ENTRA_GRAPH_SCOPE,
        },
    )
    access_token = token.get("access_token")
    if not access_token:
        raise ApiError(502, "Microsoft Entra ID did not return a Graph access token")
    query = urlencode({"$select": ENTRA_GRAPH_SELECT, "$top": "50"})
    url = f"https://graph.microsoft.com/v1.0/users?{query}"
    users: list[dict] = []
    max_pages = int(os.environ.get("GATEWATCH_ENTRA_MAX_GRAPH_PAGES", "20"))
    for _ in range(max_pages):
        payload = http_get_json(url, {"Authorization": f"Bearer {access_token}"})
        page = payload.get("value", [])
        if not isinstance(page, list):
            raise ApiError(502, "Microsoft Graph returned invalid users payload")
        users.extend([item for item in page if isinstance(item, dict)])
        next_link = payload.get("@odata.nextLink")
        if not next_link:
            return users
        url = str(next_link)
    raise ApiError(502, "Microsoft Graph users payload exceeded the configured page limit")


def graph_user_to_employee(user: dict) -> dict:
    entra_id = normalize_text(user.get("id"), "Entra ID", required=True, maximum=160)
    upn = normalize_text(user.get("userPrincipalName"), "User principal name", maximum=254).lower()
    email_value = user.get("mail") or upn
    email = normalize_email(email_value, required=True)
    employee_id = normalize_text(user.get("employeeId") or upn or entra_id, "Employee ID", required=True, maximum=80)
    account_enabled = user.get("accountEnabled")
    status = "disabled" if account_enabled is False else "active"
    return {
        "employee_id": employee_id,
        "name": normalize_text(user.get("displayName") or upn or email, "Name", required=True, maximum=160),
        "email": email,
        "department": normalize_text(user.get("department"), "Department", maximum=120),
        "title": normalize_text(user.get("jobTitle"), "Title", maximum=120),
        "location": normalize_text(user.get("officeLocation"), "Location", maximum=120),
        "status": status,
        "entra_id": entra_id,
        "entra_user_principal_name": upn,
        "entra_account_enabled": 1 if account_enabled is True else 0 if account_enabled is False else None,
        "entra_synced_at": utc_now(),
    }


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        return conn

    @contextmanager
    def session(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    department TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    manager TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'terminated')),
                    entra_id TEXT NOT NULL DEFAULT '',
                    entra_user_principal_name TEXT NOT NULL DEFAULT '',
                    entra_account_enabled INTEGER,
                    entra_synced_at TEXT NOT NULL DEFAULT '',
                    request_source TEXT NOT NULL DEFAULT '',
                    access_needed TEXT NOT NULL DEFAULT '',
                    request_received INTEGER NOT NULL DEFAULT 0,
                    manager_approved INTEGER NOT NULL DEFAULT 0,
                    it_provisioned INTEGER NOT NULL DEFAULT 0,
                    employee_notified INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER,
                    actor TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name);
                CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status);
                CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);
                """
            )
            self._migrate_employee_status_check(conn)
            self._migrate_employee_columns(conn)
            self._ensure_employee_indexes(conn)

    def _ensure_employee_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_entra_id ON employees(entra_id) WHERE entra_id != ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at)")

    def _migrate_employee_status_check(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'employees'"
        ).fetchone()
        table_sql = row["sql"] if row else ""
        if "CHECK (status IN ('active', 'terminated'))" not in table_sql:
            return

        conn.execute("ALTER TABLE employees RENAME TO employees_legacy")
        conn.executescript(
            """
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                manager TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'terminated')),
                entra_id TEXT NOT NULL DEFAULT '',
                entra_user_principal_name TEXT NOT NULL DEFAULT '',
                entra_account_enabled INTEGER,
                entra_synced_at TEXT NOT NULL DEFAULT '',
                request_source TEXT NOT NULL DEFAULT '',
                access_needed TEXT NOT NULL DEFAULT '',
                request_received INTEGER NOT NULL DEFAULT 0,
                manager_approved INTEGER NOT NULL DEFAULT 0,
                it_provisioned INTEGER NOT NULL DEFAULT 0,
                employee_notified INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        old_columns = {item["name"] for item in conn.execute("PRAGMA table_info(employees_legacy)").fetchall()}
        new_columns = {item["name"] for item in conn.execute("PRAGMA table_info(employees)").fetchall()}
        common_columns = [column for column in old_columns if column in new_columns]
        column_list = ", ".join(quote_identifier(column) for column in common_columns)
        conn.execute(
            f"INSERT INTO employees ({column_list}) SELECT {column_list} FROM employees_legacy"
        )
        conn.execute("DROP TABLE employees_legacy")

    def _migrate_employee_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(employees)").fetchall()}
        additions = {
            "title": "TEXT NOT NULL DEFAULT ''",
            "entra_id": "TEXT NOT NULL DEFAULT ''",
            "entra_user_principal_name": "TEXT NOT NULL DEFAULT ''",
            "entra_account_enabled": "INTEGER",
            "entra_synced_at": "TEXT NOT NULL DEFAULT ''",
            "request_source": "TEXT NOT NULL DEFAULT ''",
            "access_needed": "TEXT NOT NULL DEFAULT ''",
            "request_received": "INTEGER NOT NULL DEFAULT 0",
            "manager_approved": "INTEGER NOT NULL DEFAULT 0",
            "it_provisioned": "INTEGER NOT NULL DEFAULT 0",
            "employee_notified": "INTEGER NOT NULL DEFAULT 0",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE employees ADD COLUMN {quote_identifier(column)} {definition}")

    def employee_payload(self, payload: dict, *, partial: bool = False) -> dict:
        fields = {
            "employee_id": ("Key Fob ID", 80, True),
            "name": ("Name", 160, True),
            "email": ("Email", 254, True),
            "department": ("Department", 120, False),
            "title": ("Title", 120, False),
            "location": ("Location", 120, False),
            "manager": ("Manager", 160, False),
            "request_source": ("Request source", 80, False),
            "access_needed": ("Access needed", 1000, False),
            "notes": ("Notes", 2000, False),
        }
        data = {}
        for key, (label, maximum, required) in fields.items():
            if key not in payload:
                if partial:
                    continue
                if key == "email":
                    data[key] = normalize_email("", required=required)
                else:
                    data[key] = normalize_text("", label, required=required, maximum=maximum)
                continue
            if key == "email":
                data[key] = normalize_email(payload.get(key), required=required and not partial)
            else:
                data[key] = normalize_text(payload.get(key), label, required=required and not partial, maximum=maximum)
        if "status" in payload or not partial:
            data["status"] = normalize_status(payload.get("status"))
        for field in CHECKLIST_FIELDS:
            if field in payload or not partial:
                data[field] = normalize_bool_int(payload.get(field))
        return data

    def summary(self) -> dict:
        with self.session() as conn:
            one = lambda sql, params=(): conn.execute(sql, params).fetchone()[0]
            today_text = utc_now()[:10]
            return {
                "total": one("SELECT COUNT(*) FROM employees"),
                "active": one("SELECT COUNT(*) FROM employees WHERE status = 'active'"),
                "disabled": one("SELECT COUNT(*) FROM employees WHERE status = 'disabled'"),
                "terminated": one("SELECT COUNT(*) FROM employees WHERE status = 'terminated'"),
                "inProgress": one(
                    """
                    SELECT COUNT(*)
                      FROM employees
                     WHERE (access_needed != '' OR request_received = 1)
                       AND employee_notified = 0
                    """
                ),
                "updatedToday": one(
                    "SELECT COUNT(*) FROM employees WHERE substr(updated_at, 1, 10) = ?",
                    [today_text],
                ),
            }

    def health(self) -> dict:
        with self.session() as conn:
            conn.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "service": "gatewatch",
            "database": "ok",
            "checked_at": utc_now(),
        }

    def list_employees(self, query: str = "") -> list[dict]:
        search = query.strip().lower()
        with self.session() as conn:
            if not search:
                rows = conn.execute(
                    """
                    SELECT *
                      FROM employees
                     ORDER BY lower(name), id
                    """
                ).fetchall()
            else:
                like = f"%{search}%"
                rows = conn.execute(
                    """
                    SELECT *
                      FROM employees
                     WHERE lower(employee_id) LIKE ?
                        OR lower(name) LIKE ?
                        OR lower(email) LIKE ?
                        OR lower(department) LIKE ?
                        OR lower(title) LIKE ?
                        OR lower(location) LIKE ?
                        OR lower(manager) LIKE ?
                        OR lower(entra_user_principal_name) LIKE ?
                        OR lower(request_source) LIKE ?
                        OR lower(access_needed) LIKE ?
                     ORDER BY lower(name), id
                    """,
                    [like, like, like, like, like, like, like, like, like, like],
                ).fetchall()
            return rows_to_dicts(rows)

    def sync_entra_users(self, users: list[dict], actor: str = "Microsoft Entra ID") -> dict:
        result = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "disabled": 0,
            "errors": [],
        }
        now = utc_now()
        with self.session() as conn:
            for user in users:
                try:
                    data = graph_user_to_employee(user)
                except ApiError as exc:
                    result["skipped"] += 1
                    if len(result["errors"]) < 5:
                        result["errors"].append(exc.message)
                    continue
                data["entra_synced_at"] = now
                if data["status"] == "disabled":
                    result["disabled"] += 1

                before = self._find_entra_employee(conn, data)
                if before:
                    changed = {
                        key: value
                        for key, value in data.items()
                        if key != "entra_synced_at" and before.get(key) != value
                    }
                    if not changed:
                        conn.execute(
                            "UPDATE employees SET entra_synced_at = ? WHERE id = ?",
                            [now, before["id"]],
                        )
                        result["unchanged"] += 1
                        continue
                    changed["entra_synced_at"] = now
                    changed["updated_at"] = now
                    assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in changed)
                    try:
                        conn.execute(
                            f"UPDATE employees SET {assignments} WHERE id = :id",
                            {**changed, "id": before["id"]},
                        )
                    except sqlite3.IntegrityError as exc:
                        result["skipped"] += 1
                        if len(result["errors"]) < 5:
                            result["errors"].append(f"Duplicate employee ID or email for {data['email']}")
                        continue
                    after = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [before["id"]]).fetchone())
                    self._audit(
                        conn,
                        "sync",
                        "employee",
                        after["id"],
                        actor,
                        f"Synced Entra ID user {after['name']}.",
                        before,
                        after,
                    )
                    result["updated"] += 1
                    continue

                insert_data = {
                    **data,
                    "manager": "",
                    "request_source": "Entra ID",
                    "access_needed": "",
                    "request_received": 0,
                    "manager_approved": 0,
                    "it_provisioned": 0,
                    "employee_notified": 0,
                    "notes": "",
                    "created_at": now,
                    "updated_at": now,
                }
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO employees (
                            employee_id, name, email, department, title, location, manager,
                            status, entra_id, entra_user_principal_name, entra_account_enabled,
                            entra_synced_at, request_source, access_needed, request_received,
                            manager_approved, it_provisioned, employee_notified,
                            notes, created_at, updated_at
                        )
                        VALUES (
                            :employee_id, :name, :email, :department, :title, :location, :manager,
                            :status, :entra_id, :entra_user_principal_name, :entra_account_enabled,
                            :entra_synced_at, :request_source, :access_needed, :request_received,
                            :manager_approved, :it_provisioned, :employee_notified,
                            :notes, :created_at, :updated_at
                        )
                        """,
                        insert_data,
                    )
                except sqlite3.IntegrityError:
                    result["skipped"] += 1
                    if len(result["errors"]) < 5:
                        result["errors"].append(f"Duplicate employee ID or email for {data['email']}")
                    continue
                created = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [cursor.lastrowid]).fetchone())
                self._audit(
                    conn,
                    "sync",
                    "employee",
                    created["id"],
                    actor,
                    f"Created Entra ID user {created['name']}.",
                    None,
                    created,
                )
                result["created"] += 1
        result["total"] = result["created"] + result["updated"] + result["unchanged"]
        return result

    def _find_entra_employee(self, conn: sqlite3.Connection, data: dict) -> dict | None:
        row = conn.execute(
            """
            SELECT *
              FROM employees
             WHERE (entra_id != '' AND entra_id = ?)
                OR lower(email) = ?
                OR lower(employee_id) = ?
                OR (entra_user_principal_name != '' AND lower(entra_user_principal_name) = ?)
             ORDER BY
                CASE
                  WHEN entra_id != '' AND entra_id = ? THEN 0
                  WHEN lower(email) = ? THEN 1
                  WHEN lower(employee_id) = ? THEN 2
                  ELSE 3
                END,
                id
             LIMIT 1
            """,
            [
                data["entra_id"],
                data["email"].lower(),
                data["employee_id"].lower(),
                data["entra_user_principal_name"].lower(),
                data["entra_id"],
                data["email"].lower(),
                data["employee_id"].lower(),
            ],
        ).fetchone()
        return row_to_dict(row)

    def get_employee(self, employee_id: int) -> dict:
        with self.session() as conn:
            employee = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
        if not employee:
            raise ApiError(404, "Employee was not found")
        return employee

    def create_employee(self, payload: dict, actor: str = "Local user") -> dict:
        data = self.employee_payload(payload)
        now = utc_now()
        data["created_at"] = now
        data["updated_at"] = now
        with self.session() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO employees (
                        employee_id, name, email, department, title, location, manager,
                        status, request_source, access_needed, request_received,
                        manager_approved, it_provisioned, employee_notified,
                        notes, created_at, updated_at
                    )
                    VALUES (
                        :employee_id, :name, :email, :department, :title, :location, :manager,
                        :status, :request_source, :access_needed, :request_received,
                        :manager_approved, :it_provisioned, :employee_notified,
                        :notes, :created_at, :updated_at
                    )
                    """,
                    data,
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Key Fob ID or email already exists") from exc
            created = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [cursor.lastrowid]).fetchone())
            self._audit(conn, "create", "employee", created["id"], actor, f"Created employee {created['name']}.", None, created)
            return created

    def update_employee(self, employee_id: int, payload: dict, actor: str = "Local user") -> dict:
        data = self.employee_payload(payload, partial=True)
        if not data:
            raise ApiError(400, "No employee fields were provided")
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            if not before:
                raise ApiError(404, "Employee was not found")
            assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in data)
            try:
                conn.execute(
                    f"UPDATE employees SET {assignments} WHERE id = :id",
                    {**data, "id": employee_id},
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Key Fob ID or email already exists") from exc
            after = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            self._audit(conn, "update", "employee", employee_id, actor, f"Updated employee {after['name']}.", before, after)
            return after

    def delete_employee(self, employee_id: int, actor: str = "Local user") -> dict:
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            if not before:
                raise ApiError(404, "Employee was not found")
            self._delete_legacy_employee_references(conn, employee_id)
            conn.execute("DELETE FROM employees WHERE id = ?", [employee_id])
            self._audit(conn, "delete", "employee", employee_id, actor, f"Deleted employee {before['name']}.", before, None)
            return before

    def _delete_legacy_employee_references(self, conn: sqlite3.Connection, employee_id: int) -> None:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for row in tables:
            table = row["name"]
            if table in {"employees", "audit_log", "sqlite_sequence"}:
                continue
            references = conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})").fetchall()
            employee_columns = [ref["from"] for ref in references if ref["table"] == "employees"]
            for column in employee_columns:
                conn.execute(
                    f"DELETE FROM {quote_identifier(table)} WHERE {quote_identifier(column)} = ?",
                    [employee_id],
                )

    def audit_log(self) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM audit_log
                 ORDER BY id DESC
                 LIMIT 50
                """
            ).fetchall()
        return rows_to_dicts(rows)

    def audit_log_csv(self) -> str:
        rows = self.audit_log()
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["created_at", "actor", "action", "entity_type", "entity_id", "summary"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_safe_cell(row.get(key)) for key in writer.fieldnames})
        return output.getvalue()

    def _audit(
        self,
        conn: sqlite3.Connection,
        action: str,
        entity_type: str,
        entity_id: int | None,
        actor: str,
        summary: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (
                created_at, action, entity_type, entity_id, actor, summary, before_json, after_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                utc_now(),
                action,
                entity_type,
                entity_id,
                actor,
                summary,
                json.dumps(before, sort_keys=True) if before else None,
                json.dumps(after, sort_keys=True) if after else None,
            ],
        )


class GatewatchServer(ThreadingHTTPServer):
    daemon_threads = True


def make_handler(store: Store, static_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Gatewatch/2.0"
        protocol_version = "HTTP/1.1"

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._common_headers()
            self.end_headers()

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def log_message(self, format: str, *args) -> None:
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

        def _dispatch(self, method: str) -> None:
            try:
                parsed = urlparse(self.path)
                if method == "GET" and parsed.path == "/healthz":
                    self._send_json(store.health())
                    return
                if parsed.path.startswith("/auth/"):
                    self._handle_auth(method, parsed.path, parse_qs(parsed.query))
                    return
                if parsed.path.startswith("/api/"):
                    self._handle_api(method, parsed.path, parse_qs(parsed.query))
                    return
                if method == "GET":
                    self._serve_static(parsed.path)
                    return
                raise ApiError(405, "Method not allowed")
            except ApiError as exc:
                self._send_json({"error": exc.message}, exc.status)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return
            except Exception as exc:
                parsed_path = urlparse(self.path).path
                sys.stderr.write(f"Unhandled {exc.__class__.__name__} while handling {method} {parsed_path}\n")
                self._send_json({"error": "Internal server error"}, 500)

        def _handle_auth(self, method: str, path: str, query: dict) -> None:
            if method == "GET" and path == "/auth/entra/login":
                config = entra_config()
                if not config["sso_configured"]:
                    raise ApiError(503, "Microsoft Entra ID SSO is not configured")
                state = secrets.token_urlsafe(32)
                nonce = secrets.token_urlsafe(32)
                verifier = secrets.token_urlsafe(48)
                challenge = base64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
                oauth_state = signed_payload(
                    {
                        "state": state,
                        "nonce": nonce,
                        "verifier": verifier,
                        "exp": time.time() + 600,
                    }
                )
                location = (
                    f"{entra_authority_path('authorize', config['tenant_id'])}?"
                    + urlencode(
                        {
                            "client_id": config["client_id"],
                            "response_type": "code",
                            "redirect_uri": config["redirect_uri"],
                            "response_mode": "query",
                            "scope": ENTRA_SIGNIN_SCOPES,
                            "state": state,
                            "nonce": nonce,
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                        }
                    )
                )
                self._send_redirect(
                    location,
                    cookies=[
                        make_cookie(
                            OAUTH_COOKIE,
                            oauth_state,
                            max_age=600,
                            path="/auth/entra",
                            secure=config["redirect_uri"].startswith("https://"),
                        )
                    ],
                )
                return

            if method == "GET" and path == "/auth/entra/callback":
                error = query.get("error", [""])[0]
                if error:
                    description = query.get("error_description", ["Microsoft Entra ID sign-in failed"])[0]
                    raise ApiError(401, description)
                code = query.get("code", [""])[0]
                returned_state = query.get("state", [""])[0]
                if not code or not returned_state:
                    raise ApiError(400, "Microsoft Entra ID callback was missing code or state")
                cookies = parse_cookies(self.headers.get("Cookie"))
                oauth_state = unsign_payload(cookies.get(OAUTH_COOKIE))
                if not oauth_state or not hmac.compare_digest(str(oauth_state.get("state")), returned_state):
                    raise ApiError(401, "Microsoft Entra ID sign-in state did not match")
                config = entra_config()
                if not config["sso_configured"]:
                    raise ApiError(503, "Microsoft Entra ID SSO is not configured")
                token = http_post_form(
                    entra_authority_path("token", config["tenant_id"]),
                    {
                        "client_id": config["client_id"],
                        "client_secret": config["client_secret"],
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": config["redirect_uri"],
                        "scope": ENTRA_SIGNIN_SCOPES,
                        "code_verifier": oauth_state.get("verifier", ""),
                    },
                )
                access_token = token.get("access_token")
                if not access_token:
                    raise ApiError(502, "Microsoft Entra ID did not return a delegated access token")
                signed_in = fetch_graph_me(access_token)
                authorization = resolve_session_authorization(access_token)
                try:
                    token_lifetime = int(token.get("expires_in", 3600))
                except (TypeError, ValueError):
                    token_lifetime = 3600
                expires_at = int(time.time() + min(token_lifetime, 28800))
                session = signed_payload(
                    {
                        "sub": signed_in.get("id", ""),
                        "tid": config["tenant_id"],
                        "name": signed_in.get("displayName") or signed_in.get("userPrincipalName") or signed_in.get("mail") or "Entra user",
                        "email": signed_in.get("mail") or signed_in.get("userPrincipalName") or "",
                        "exp": expires_at,
                        **authorization,
                    }
                )
                self._send_redirect(
                    "/",
                    cookies=[
                        make_cookie(
                            SESSION_COOKIE,
                            session,
                            max_age=max(1, expires_at - int(time.time())),
                            secure=config["redirect_uri"].startswith("https://"),
                        ),
                        clear_cookie(OAUTH_COOKIE, path="/auth/entra"),
                    ],
                )
                return

            if method == "GET" and path == "/auth/logout":
                self._send_redirect("/", cookies=[clear_cookie(SESSION_COOKIE)])
                return

            raise ApiError(404, "Authentication route not found")

        def _request_actor(self) -> str:
            session = current_session(self.headers)
            return session_actor(session)

        def _require_employee_modify(self) -> None:
            session = current_session(self.headers)
            if session and session.get("can_modify_employees"):
                return
            raise ApiError(
                403,
                f"Only members of {admin_group_canonical()} can edit, delete, sync, or view admin configuration",
            )

        def _guard_same_origin_mutation(self, method: str) -> None:
            if method not in {"POST", "PATCH", "DELETE"}:
                return
            host = self.headers.get("Host", "")
            for header in ("Origin", "Referer"):
                value = self.headers.get(header, "").strip()
                if not value:
                    continue
                parsed = urlparse(value)
                if parsed.netloc and parsed.netloc != host:
                    raise ApiError(403, "Cross-origin write requests are not allowed")

        def _handle_api(self, method: str, path: str, query: dict) -> None:
            self._guard_same_origin_mutation(method)
            actor = self._request_actor()
            if method == "GET" and path == "/api/auth/status":
                self._send_json(auth_status_payload(self.headers))
                return
            if method == "GET" and path == "/api/admin/config":
                self._require_employee_modify()
                self._send_json({"config": admin_config_payload()})
                return
            if method == "POST" and path == "/api/admin/config/validate":
                self._require_employee_modify()
                self._send_json({"preview": admin_config_preview(self._read_json())})
                return
            if method == "GET" and path == "/api/bootstrap":
                self._send_json(
                    {
                        "summary": store.summary(),
                        "employees": store.list_employees(query.get("q", [""])[0]),
                        "audit": store.audit_log(),
                        "auth": auth_status_payload(self.headers)["entra"],
                    }
                )
                return
            if method == "POST" and path == "/api/entra/sync":
                self._require_employee_modify()
                users = fetch_graph_users()
                self._send_json({"sync": store.sync_entra_users(users, actor=actor)})
                return
            if method == "GET" and path == "/api/employees":
                self._send_json({"employees": store.list_employees(query.get("q", [""])[0])})
                return
            if method == "POST" and path == "/api/employees":
                self._send_json({"employee": store.create_employee(self._read_json(), actor=actor)}, 201)
                return
            if method == "GET" and path.startswith("/api/employees/"):
                self._send_json({"employee": store.get_employee(self._path_int(path, "/api/employees/"))})
                return
            if method == "PATCH" and path.startswith("/api/employees/"):
                self._require_employee_modify()
                employee_id = self._path_int(path, "/api/employees/")
                self._send_json({"employee": store.update_employee(employee_id, self._read_json(), actor=actor)})
                return
            if method == "DELETE" and path.startswith("/api/employees/"):
                self._require_employee_modify()
                employee_id = self._path_int(path, "/api/employees/")
                self._send_json({"employee": store.delete_employee(employee_id, actor=actor)})
                return
            if method == "GET" and path == "/api/audit-log":
                self._send_json({"audit": store.audit_log()})
                return
            if method == "GET" and path == "/api/audit-log.csv":
                self._send_text(store.audit_log_csv(), "text/csv; charset=utf-8")
                return
            raise ApiError(404, "API route not found")

        def _path_int(self, path: str, prefix: str) -> int:
            value = path.removeprefix(prefix).split("/", 1)[0]
            try:
                return int(value)
            except ValueError as exc:
                raise ApiError(400, "Invalid employee ID") from exc

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ApiError(400, "Content-Length must be a valid integer") from exc
            if length < 0:
                raise ApiError(400, "Content-Length must be a valid integer")
            if length > MAX_JSON_BODY_BYTES:
                raise ApiError(413, f"Request body must be {MAX_JSON_BODY_BYTES} bytes or smaller")
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ApiError(400, "Request body must be valid JSON") from exc
            if not isinstance(payload, dict):
                raise ApiError(400, "Request body must be a JSON object")
            return payload

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self._common_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._common_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_redirect(self, location: str, cookies: list[str] | None = None) -> None:
            body = b""
            self.send_response(302)
            self._common_headers()
            self.send_header("Location", location)
            for cookie in cookies or []:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _common_headers(self) -> None:
            self.close_connection = True
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)

        def _serve_static(self, path: str) -> None:
            if path == "/":
                path = "/index.html"
            candidate = (static_dir / path.lstrip("/")).resolve()
            try:
                candidate.relative_to(static_dir.resolve())
            except ValueError as exc:
                raise ApiError(403, "Static path is outside the web directory") from exc
            if not candidate.exists() or not candidate.is_file():
                raise ApiError(404, "Static file not found")
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_response(200)
            self._common_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def run(host: str = "127.0.0.1", port: int = 8087, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    validate_startup_security(host)
    store = Store(db_path)
    store.init()
    handler = make_handler(store, STATIC_DIR)
    server = GatewatchServer((host, port), handler)
    print(f"Gatewatch running at http://{host}:{port}")
    print(f"SQLite database: {Path(db_path).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Gatewatch.")
    finally:
        server.server_close()


if __name__ == "__main__":
    host = os.environ.get("GATEWATCH_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWATCH_PORT", "8087"))
    db_path = Path(os.environ.get("GATEWATCH_DB", DEFAULT_DB_PATH))
    run(host, port, db_path)
