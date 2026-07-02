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
import platform
import re
import shlex
import sqlite3
import sys
import secrets
import subprocess
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
CSRF_HEADER = "X-Gatewatch-CSRF"
CSRF_TOKEN_SECONDS = 8 * 60 * 60
EMPLOYEE_STATUSES = {"active", "disabled", "terminated"}
CHANGE_REQUEST_STATUSES = {"pending", "approved", "rejected"}
SESSION_COOKIE = "gatewatch_session"
OAUTH_COOKIE = "gatewatch_oauth"
AUTH_MODE_LOCAL = "local"
AUTH_MODE_TRUSTED_PROXY = "trusted_proxy"
PROXY_SECRET_HEADERS = ("X-Gatewatch-Proxy-Secret", "X-Access-Register-Proxy-Secret")
PROXY_SECRET_MIN_LENGTH = 16
RUNTIME_CONFIG_KEYS = (
    "GATEWATCH_HOST",
    "GATEWATCH_PORT",
    "GATEWATCH_DB",
    "GATEWATCH_ALLOW_INSECURE_NETWORK",
    "GATEWATCH_AUTH_MODE",
    "GATEWATCH_PROXY_SECRET",
    "GATEWATCH_SESSION_SECRET",
    "GATEWATCH_ENTRA_TENANT_ID",
    "GATEWATCH_ENTRA_CLIENT_ID",
    "GATEWATCH_ENTRA_CLIENT_SECRET",
    "GATEWATCH_ENTRA_REDIRECT_URI",
    "GATEWATCH_ADMIN_GROUP_CANONICAL",
    "GATEWATCH_SUPERVISOR_GROUP_CANONICAL",
    "GATEWATCH_UPDATE_MODE",
    "GATEWATCH_UPDATE_BRANCH",
    "GATEWATCH_UPDATE_SOURCE_URL",
    "GATEWATCH_UPDATE_DATA_DIR",
    "GATEWATCH_UPDATE_INSTALL_DIR",
    "GATEWATCH_UPDATE_SERVICE_NAME",
    "GATEWATCH_UPDATE_STATUS_FILE",
    "GATEWATCH_UPDATE_LOG_FILE",
    "GATEWATCH_UPDATE_COMMAND",
)


def default_runtime_config_file() -> Path:
    if os.name == "nt":
        return DEFAULT_DB_PATH.parent / "gatewatch.env"
    return Path("/etc/gatewatch/gatewatch.env")


def runtime_config_file() -> Path:
    configured = os.environ.get("GATEWATCH_CONFIG_FILE", "").strip()
    return Path(configured) if configured else default_runtime_config_file()


def parse_env_file_value(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        value = []
        escaped = False
        for char in text[1:-1]:
            if escaped:
                value.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            else:
                value.append(char)
        if escaped:
            value.append("\\")
        return "".join(value)
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    return text.split("#", 1)[0].strip()


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        key, separator, raw_value = stripped.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        values[key] = parse_env_file_value(raw_value)
    return values


def load_runtime_config_file() -> None:
    path = runtime_config_file()
    values = read_env_file(path)
    for key in RUNTIME_CONFIG_KEYS:
        if key in values:
            os.environ[key] = values[key]
    if values:
        os.environ.setdefault("GATEWATCH_CONFIG_FILE", str(path))


load_runtime_config_file()
SESSION_SECRET = os.environ.get("GATEWATCH_SESSION_SECRET") or secrets.token_urlsafe(48)
ENTRA_SIGNIN_SCOPES = "openid profile email offline_access User.Read"
ENTRA_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_ADMIN_GROUP_CANONICAL = "gcefcu.org/Users/Domain Admins"
DEFAULT_SUPERVISOR_GROUP_CANONICAL = "gcefcu.org/Users/Gatewatch Supervisors"
DEFAULT_UPDATE_BRANCH = "main"
DEFAULT_UPDATE_SOURCE_URL = "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"
UPDATE_MODES = {"auto", "volume", "systemd"}
BACKGROUND_UPDATE_PROCESSES: list[subprocess.Popen] = []
ENTRA_GRAPH_SELECT = ",".join(
    [
        "id",
        "displayName",
        "mail",
        "userPrincipalName",
        "department",
        "jobTitle",
        "officeLocation",
        "businessPhones",
        "mobilePhone",
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
ACCESS_FIELD_TYPES = {"text", "textarea", "checkbox", "date", "select"}
ACCESS_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
DEFAULT_ACCESS_FIELDS = (
    {
        "key": "request_type",
        "label": "Request Type",
        "section": "Request Details",
        "field_type": "select",
        "options": ["New User", "Change Access", "Delete User"],
        "required": 0,
        "sort_order": 10,
    },
    {
        "key": "effective_date",
        "label": "Effective Date",
        "section": "Request Details",
        "field_type": "date",
        "options": [],
        "required": 0,
        "sort_order": 20,
    },
    {
        "key": "branch",
        "label": "Branch",
        "section": "Employee Information",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 30,
    },
    {
        "key": "active_directory",
        "label": "Active Directory",
        "section": "Network",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 40,
    },
    {
        "key": "email_account",
        "label": "Email",
        "section": "Network",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 50,
    },
    {
        "key": "software_access",
        "label": "Software / Systems",
        "section": "Systems Access",
        "field_type": "textarea",
        "options": [],
        "required": 0,
        "sort_order": 60,
    },
    {
        "key": "xp_operator_number",
        "label": "XP User Operator Number",
        "section": "XP2",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 70,
    },
    {
        "key": "xp_user_id",
        "label": "XP User ID",
        "section": "XP2",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 80,
    },
    {
        "key": "physical_security",
        "label": "Physical Security",
        "section": "Security",
        "field_type": "select",
        "options": ["None", "Alarm Codes", "Combinations", "Building Key", "Fob"],
        "required": 0,
        "sort_order": 90,
    },
    {
        "key": "phone_extension",
        "label": "Phone Extension",
        "section": "Security",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 100,
    },
    {
        "key": "direct_line",
        "label": "Direct Line",
        "section": "Security",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 110,
    },
    {
        "key": "fob_pin",
        "label": "Fob PIN",
        "section": "Security",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 120,
    },
    {
        "key": "employee_memberships",
        "label": "Employee Memberships",
        "section": "Credit Union Accounts",
        "field_type": "textarea",
        "options": [],
        "required": 0,
        "sort_order": 130,
    },
    {
        "key": "relative_memberships",
        "label": "Relative Memberships",
        "section": "Credit Union Accounts",
        "field_type": "textarea",
        "options": [],
        "required": 0,
        "sort_order": 140,
    },
    {
        "key": "corporate_card",
        "label": "Corporate Credit Card",
        "section": "Miscellaneous",
        "field_type": "checkbox",
        "options": [],
        "required": 0,
        "sort_order": 150,
    },
    {
        "key": "credit_limit",
        "label": "Credit Limit",
        "section": "Miscellaneous",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 160,
    },
    {
        "key": "disaster_recovery_access",
        "label": "Disaster Recovery Access",
        "section": "Miscellaneous",
        "field_type": "checkbox",
        "options": [],
        "required": 0,
        "sort_order": 170,
    },
    {
        "key": "cell_phone",
        "label": "Cell Phone",
        "section": "Miscellaneous",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 180,
    },
    {
        "key": "mis_approval",
        "label": "MIS Approval",
        "section": "Approvals",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 190,
    },
    {
        "key": "supervisor_approval",
        "label": "Supervisor Approval",
        "section": "Approvals",
        "field_type": "text",
        "options": [],
        "required": 0,
        "sort_order": 200,
    },
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
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
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


def slugify_access_key(label: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")
    if not text:
        text = "field"
    if not text[0].isalpha():
        text = f"field_{text}"
    return text[:64].rstrip("_") or "field"


def normalize_access_field_type(value) -> str:
    field_type = str(value or "text").strip().lower()
    if field_type not in ACCESS_FIELD_TYPES:
        raise ApiError(400, "Field type must be text, textarea, checkbox, date, or select")
    return field_type


def normalize_options(value) -> list[str]:
    if value is None:
        raw_values = []
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value).replace("\r\n", "\n").split("\n")
    options = []
    seen = set()
    for item in raw_values:
        text = normalize_text(item, "Field option", maximum=80)
        if not text:
            continue
        lower = text.lower()
        if lower in seen:
            continue
        seen.add(lower)
        options.append(text)
    if len(options) > 40:
        raise ApiError(400, "Field options must contain 40 items or fewer")
    return options


def normalize_access_profile(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ApiError(400, "Access profile must be an object")
    normalized = {}
    for raw_key, raw_value in value.items():
        key = normalize_text(raw_key, "Access profile key", required=True, maximum=64)
        if not ACCESS_FIELD_KEY_RE.match(key):
            raise ApiError(400, "Access profile keys must use lowercase letters, numbers, and underscores")
        if isinstance(raw_value, bool):
            normalized[key] = raw_value
            continue
        if raw_value in (None, ""):
            normalized[key] = ""
            continue
        normalized[key] = normalize_text(raw_value, "Access profile value", maximum=2000)
    return normalized


def access_profile_json(value) -> str:
    return json.dumps(normalize_access_profile(value), separators=(",", ":"), sort_keys=True)


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


def auth_mode() -> str:
    configured = (
        os.environ.get("GATEWATCH_AUTH_MODE")
        or os.environ.get("ACCESS_REGISTER_AUTH_MODE")
        or AUTH_MODE_LOCAL
    )
    text = str(configured).strip().lower().replace("-", "_")
    if text == AUTH_MODE_TRUSTED_PROXY:
        return AUTH_MODE_TRUSTED_PROXY
    return AUTH_MODE_LOCAL


def trusted_proxy_auth_enabled() -> bool:
    return auth_mode() == AUTH_MODE_TRUSTED_PROXY


def trusted_proxy_secret() -> str:
    return (
        os.environ.get("GATEWATCH_PROXY_SECRET", "").strip()
        or os.environ.get("ACCESS_REGISTER_PROXY_SECRET", "").strip()
    )


def trusted_proxy_secret_strong(secret: str | None = None) -> bool:
    return len(secret if secret is not None else trusted_proxy_secret()) >= PROXY_SECRET_MIN_LENGTH


def validate_startup_security(host: str | None) -> None:
    if trusted_proxy_auth_enabled():
        secret = trusted_proxy_secret()
        if not secret:
            raise RuntimeError("GATEWATCH_PROXY_SECRET is required when GATEWATCH_AUTH_MODE=trusted_proxy.")
        if not trusted_proxy_secret_strong(secret):
            raise RuntimeError(
                f"GATEWATCH_PROXY_SECRET must be at least {PROXY_SECRET_MIN_LENGTH} characters in trusted proxy mode."
            )
        return
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


def first_header(headers, names: tuple[str, ...] | list[str]) -> str:
    for name in names:
        value = headers.get(name)
        if value:
            return str(value).strip()
    return ""


def split_header_values(value: str | None) -> list[str]:
    return [item.strip() for item in re.split(r"[,;|]+", str(value or "")) if item.strip()]


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


def supervisor_group_canonical() -> str:
    configured = os.environ.get("GATEWATCH_SUPERVISOR_GROUP_CANONICAL", "").strip()
    return configured or DEFAULT_SUPERVISOR_GROUP_CANONICAL


def group_leaf(canonical: str) -> str:
    return canonical.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()


def admin_group_leaf() -> str:
    return group_leaf(admin_group_canonical())


def normalize_group_identifier(value) -> str:
    text = str(value or "").replace("\\", "/").strip().casefold()
    return " ".join(text.split())


def admin_group_identifiers() -> set[str]:
    return group_identifiers(admin_group_canonical())


def supervisor_group_identifiers() -> set[str]:
    return group_identifiers(supervisor_group_canonical())


def group_identifiers(canonical: str) -> set[str]:
    leaf = group_leaf(canonical)
    return {
        normalize_group_identifier(item)
        for item in [canonical, leaf]
        if normalize_group_identifier(item)
    }


def group_matches_identifiers(group: dict, expected: set[str]) -> bool:
    candidates = [
        group.get("id"),
        group.get("displayName"),
        group.get("mailNickname"),
        group.get("onPremisesSamAccountName"),
        group.get("onPremisesSecurityIdentifier"),
    ]
    return any(normalize_group_identifier(candidate) in expected for candidate in candidates)


def group_matches_admin(group: dict) -> bool:
    return group_matches_identifiers(group, admin_group_identifiers())


def group_matches_supervisor(group: dict) -> bool:
    return group_matches_identifiers(group, supervisor_group_identifiers())


def permission_role(*, can_administer: bool, can_modify: bool) -> str:
    if can_administer:
        return "admin"
    if can_modify:
        return "supervisor"
    return "user"


def trusted_proxy_session(headers) -> dict | None:
    expected_secret = trusted_proxy_secret()
    provided_secret = first_header(headers, PROXY_SECRET_HEADERS)
    if not expected_secret or not provided_secret or not hmac.compare_digest(provided_secret, expected_secret):
        raise ApiError(403, "Trusted proxy secret is missing or invalid")

    subject = first_header(headers, ("X-Remote-User", "X-Forwarded-User", "X-Authenticated-User"))
    if not subject:
        raise ApiError(401, "Authenticated proxy user header is required")

    name = first_header(headers, ("X-Remote-Name", "X-Forwarded-Name")) or subject
    email = first_header(headers, ("X-Remote-Email", "X-Forwarded-Email"))
    if not email and "@" in subject:
        email = subject
    groups = split_header_values(
        first_header(headers, ("X-Remote-Groups", "X-Forwarded-Groups", "X-Authenticated-Groups"))
    )
    expected_groups = admin_group_identifiers()
    expected_supervisor_groups = supervisor_group_identifiers()
    can_administer = any(normalize_group_identifier(group) in expected_groups for group in groups)
    can_modify = can_administer or any(
        normalize_group_identifier(group) in expected_supervisor_groups for group in groups
    )
    session = {
        "name": name,
        "email": email,
        "tenant_id": first_header(headers, ("X-Remote-Tenant", "X-Forwarded-Tenant")) or "trusted-proxy",
        "can_modify_employees": can_modify,
        "can_delete_employees": can_administer,
        "can_administer_system": can_administer,
        "can_manage_templates": can_modify,
        "admin_group": admin_group_canonical(),
        "supervisor_group": supervisor_group_canonical(),
        "group_check_error": "" if groups else "No authenticated proxy groups were provided.",
        "groups_checked_at": utc_now(),
        "auth_mode": AUTH_MODE_TRUSTED_PROXY,
        "subject": subject,
    }
    session["role"] = permission_role(can_administer=can_administer, can_modify=can_modify)
    session["actor"] = session_actor(session)
    return session


def session_actor(session: dict | None) -> str:
    if not session:
        return "Local user"
    name = str(session.get("name") or "").strip()
    email = str(session.get("email") or "").strip()
    if name and email and name.casefold() != email.casefold():
        return f"{name} ({email})"
    return email or name or "Entra user"


def current_session(headers) -> dict | None:
    if trusted_proxy_auth_enabled():
        return trusted_proxy_session(headers)
    cookies = parse_cookies(headers.get("Cookie"))
    session = unsign_payload(cookies.get(SESSION_COOKIE))
    if not session:
        return None
    explicit_admin_flag = "can_administer_system" in session or "can_delete_employees" in session
    can_administer = bool(
        session.get("can_administer_system")
        or session.get("can_delete_employees")
        or (session.get("can_modify_employees") and not explicit_admin_flag)
    )
    can_modify = bool(session.get("can_modify_employees") or can_administer)
    current = {
        "name": session.get("name") or session.get("email") or "Entra user",
        "email": session.get("email") or "",
        "tenant_id": session.get("tid") or "",
        "can_modify_employees": can_modify,
        "can_delete_employees": can_administer,
        "can_administer_system": can_administer,
        "can_manage_templates": can_modify,
        "admin_group": session.get("admin_group") or admin_group_canonical(),
        "supervisor_group": session.get("supervisor_group") or supervisor_group_canonical(),
        "group_check_error": session.get("group_check_error") or "",
        "groups_checked_at": session.get("groups_checked_at") or "",
        "role": session.get("role") or permission_role(can_administer=can_administer, can_modify=can_modify),
    }
    current["actor"] = session_actor(current)
    return current


def csrf_subject(session: dict) -> str:
    subject = (
        session.get("subject")
        or session.get("email")
        or session.get("name")
        or session_actor(session)
    )
    tenant = session.get("tenant_id") or session.get("tid") or ""
    return f"{auth_mode()}|{tenant}|{subject}"


def csrf_token_for_session(session: dict | None) -> str:
    if not session:
        return ""
    return signed_payload(
        {
            "csrf": secrets.token_urlsafe(24),
            "subject": csrf_subject(session),
            "exp": time.time() + CSRF_TOKEN_SECONDS,
        }
    )


def verify_csrf_token(headers, session: dict | None) -> None:
    if not session:
        return
    token = first_header(headers, (CSRF_HEADER,))
    payload = unsign_payload(token)
    if not payload or not payload.get("csrf"):
        raise ApiError(403, "CSRF token is missing or invalid")
    if not hmac.compare_digest(str(payload.get("subject") or ""), csrf_subject(session)):
        raise ApiError(403, "CSRF token is missing or invalid")


def auth_permissions_payload(headers) -> dict:
    session = current_session(headers)
    can_modify = bool(session and session.get("can_modify_employees"))
    can_administer = bool(session and session.get("can_administer_system"))
    role = permission_role(can_administer=can_administer, can_modify=can_modify)
    if can_administer:
        reason = f"Signed in user is a member of {admin_group_canonical()}."
    elif can_modify:
        reason = (
            f"Signed in user is a supervisor from {supervisor_group_canonical()}; "
            f"{admin_group_canonical()} is still required for delete, sync, logs, and configuration."
        )
    elif session and session.get("group_check_error"):
        reason = "Group membership could not be verified; direct edits, delete, sync, logs, and configuration are locked."
    elif session:
        reason = (
            f"Members of {supervisor_group_canonical()} can edit users and templates. "
            f"Members of {admin_group_canonical()} can also delete, sync, view logs, and configure Gatewatch."
        )
    else:
        reason = (
            f"Create and request edits locally. Sign in as a supervisor or a member of "
            f"{admin_group_canonical()} for direct employee changes."
        )
    return {
        "canModifyEmployees": can_modify,
        "canDeleteEmployees": can_administer,
        "canAdministerSystem": can_administer,
        "canManageTemplates": can_modify,
        "role": role,
        "adminGroup": admin_group_canonical(),
        "supervisorGroup": supervisor_group_canonical(),
        "actor": session_actor(session),
        "reason": reason,
    }


def auth_status_payload(headers) -> dict:
    config = entra_config()
    if trusted_proxy_auth_enabled():
        session = current_session(headers)
        return {
            "entra": {
                "configured": True,
                "ssoConfigured": True,
                "graphConfigured": config["graph_configured"],
                "sessionPersistent": bool(trusted_proxy_secret()),
                "redirectUri": "",
                "loginUrl": "",
                "logoutUrl": "",
                "provider": AUTH_MODE_TRUSTED_PROXY,
                "user": session,
                "permissions": auth_permissions_payload(headers),
                "csrfToken": csrf_token_for_session(session),
            }
        }
    session = current_session(headers)
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
            "provider": AUTH_MODE_LOCAL,
            "user": session,
            "permissions": auth_permissions_payload(headers),
            "csrfToken": csrf_token_for_session(session),
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
    supervisor_group: str,
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
            "message": f"Delete, sync, logs, and configuration require {admin_group}."
            if admin_group
            else "Configure the AD group that can administer Gatewatch.",
        }
    )
    checks.append(
        {
            "key": "supervisorGroup",
            "label": "Supervisor gate",
            "status": "ok" if supervisor_group else "blocked",
            "blocked": not bool(supervisor_group),
            "message": f"Direct employee edits and access templates are available to {supervisor_group}."
            if supervisor_group
            else "Configure the AD group that can modify employees without admin-only controls.",
        }
    )
    return checks


def env_template_line(name: str, value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{name}="{text}"'


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
        env_template_line("GATEWATCH_CONFIG_FILE", config.get("config_file") or str(runtime_config_file())),
        env_template_line("GATEWATCH_AUTH_MODE", config["auth_mode"]),
        env_template_line("GATEWATCH_PROXY_SECRET", config["proxy_secret"]),
        env_template_line("GATEWATCH_SESSION_SECRET", config["session_secret"]),
        env_template_line("GATEWATCH_ENTRA_TENANT_ID", config["tenant_id"]),
        env_template_line("GATEWATCH_ENTRA_CLIENT_ID", config["client_id"]),
        env_template_line("GATEWATCH_ENTRA_CLIENT_SECRET", config["client_secret"]),
        env_template_line("GATEWATCH_ENTRA_REDIRECT_URI", config["redirect_uri"]),
        env_template_line("GATEWATCH_ADMIN_GROUP_CANONICAL", config["admin_group"]),
        env_template_line("GATEWATCH_SUPERVISOR_GROUP_CANONICAL", config["supervisor_group"]),
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
            supervisor_group=config["supervisor_group"],
        )
    )
    if config["auth_mode"] == AUTH_MODE_TRUSTED_PROXY:
        proxy_secret_configured = bool(config.get("proxy_secret_configured"))
        proxy_secret_strong = bool(config.get("proxy_secret_strong"))
        proxy_secret_ok = proxy_secret_configured and proxy_secret_strong
        checks.append(
            {
                "key": "proxySecret",
                "label": "Trusted proxy secret",
                "status": "ok" if proxy_secret_ok else "blocked",
                "blocked": not proxy_secret_ok,
                "message": "Trusted proxy mode has a shared secret for proxy-injected identity headers."
                if proxy_secret_ok
                else f"Set GATEWATCH_PROXY_SECRET to at least {PROXY_SECRET_MIN_LENGTH} characters before enabling trusted proxy mode.",
            }
        )
    return checks


def config_file_status(path: Path | None = None) -> dict:
    target = path or runtime_config_file()
    status = path_status(target)
    writable = False
    if status["exists"] and os.access(status["path"], os.W_OK):
        writable = True
    elif status["parentExists"] and os.access(status["parent"], os.W_OK):
        writable = True
    status["writable"] = writable
    return status


def current_update_env_values() -> dict[str, str]:
    return {
        "GATEWATCH_UPDATE_MODE": os.environ.get("GATEWATCH_UPDATE_MODE", "").strip(),
        "GATEWATCH_UPDATE_BRANCH": os.environ.get("GATEWATCH_UPDATE_BRANCH", "").strip(),
        "GATEWATCH_UPDATE_SOURCE_URL": os.environ.get("GATEWATCH_UPDATE_SOURCE_URL", "").strip(),
        "GATEWATCH_UPDATE_DATA_DIR": os.environ.get("GATEWATCH_UPDATE_DATA_DIR", "").strip(),
        "GATEWATCH_UPDATE_INSTALL_DIR": os.environ.get("GATEWATCH_UPDATE_INSTALL_DIR", "").strip(),
        "GATEWATCH_UPDATE_SERVICE_NAME": os.environ.get("GATEWATCH_UPDATE_SERVICE_NAME", "").strip(),
        "GATEWATCH_UPDATE_STATUS_FILE": os.environ.get("GATEWATCH_UPDATE_STATUS_FILE", "").strip(),
        "GATEWATCH_UPDATE_LOG_FILE": os.environ.get("GATEWATCH_UPDATE_LOG_FILE", "").strip(),
        "GATEWATCH_UPDATE_COMMAND": os.environ.get("GATEWATCH_UPDATE_COMMAND", "").strip(),
    }


def admin_config_from_payload(payload: dict) -> tuple[dict, dict[str, str]]:
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
    supervisor_group = normalize_text(
        payload.get("supervisorGroupCanonical") or DEFAULT_SUPERVISOR_GROUP_CANONICAL,
        "Supervisor group",
        maximum=240,
    )
    client_secret_input = normalize_text(payload.get("clientSecret"), "Entra client secret", maximum=1000)
    session_secret_input = normalize_text(payload.get("sessionSecret"), "Session secret", maximum=1000)
    client_secret_provided = bool(client_secret_input)
    session_secret_provided = bool(session_secret_input)
    client_secret_value = client_secret_input or os.environ.get("GATEWATCH_ENTRA_CLIENT_SECRET", "").strip()
    session_secret_value = session_secret_input or os.environ.get("GATEWATCH_SESSION_SECRET", "").strip()
    auth_mode_value = auth_mode()
    proxy_secret_value = trusted_proxy_secret()
    proxy_secret_display = "<already set on server>" if proxy_secret_value else "<paste value here>"
    proxy_secret_strong = trusted_proxy_secret_strong(proxy_secret_value)
    config_path = str(runtime_config_file())
    config = {
        "host": host,
        "port": port,
        "database_path": database_path,
        "config_file": config_path,
        "auth_mode": auth_mode_value,
        "proxy_secret": proxy_secret_display,
        "proxy_secret_configured": bool(proxy_secret_value),
        "proxy_secret_strong": proxy_secret_strong,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": secret_placeholder("GATEWATCH_ENTRA_CLIENT_SECRET", provided=client_secret_provided),
        "client_secret_configured": bool(client_secret_value),
        "redirect_uri": redirect_uri,
        "admin_group": admin_group,
        "supervisor_group": supervisor_group,
        "session_secret": secret_placeholder("GATEWATCH_SESSION_SECRET", provided=session_secret_provided),
        "session_secret_configured": bool(session_secret_value),
        "allow_insecure_network": bool(payload.get("allowInsecureNetwork")),
    }
    env_values = {
        "GATEWATCH_HOST": host,
        "GATEWATCH_PORT": port,
        "GATEWATCH_DB": database_path,
        "GATEWATCH_CONFIG_FILE": config_path,
        "GATEWATCH_ALLOW_INSECURE_NETWORK": "1" if config["allow_insecure_network"] else "0",
        "GATEWATCH_AUTH_MODE": auth_mode_value,
        "GATEWATCH_PROXY_SECRET": proxy_secret_value,
        "GATEWATCH_SESSION_SECRET": session_secret_value,
        "GATEWATCH_ENTRA_TENANT_ID": tenant_id,
        "GATEWATCH_ENTRA_CLIENT_ID": client_id,
        "GATEWATCH_ENTRA_CLIENT_SECRET": client_secret_value,
        "GATEWATCH_ENTRA_REDIRECT_URI": redirect_uri,
        "GATEWATCH_ADMIN_GROUP_CANONICAL": admin_group,
        "GATEWATCH_SUPERVISOR_GROUP_CANONICAL": supervisor_group,
    }
    env_values.update(current_update_env_values())
    return config, env_values


def admin_config_payload() -> dict:
    env_config = entra_config()
    host = os.environ.get("GATEWATCH_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("GATEWATCH_PORT", "8087").strip() or "8087"
    database_path = os.environ.get("GATEWATCH_DB", str(DEFAULT_DB_PATH)).strip() or str(DEFAULT_DB_PATH)
    admin_group = admin_group_canonical()
    supervisor_group = supervisor_group_canonical()
    proxy_secret_configured = bool(trusted_proxy_secret())
    proxy_secret_strong = trusted_proxy_secret_strong()
    config = {
        "host": host,
        "port": port,
        "database_path": database_path,
        "config_file": str(runtime_config_file()),
        "auth_mode": auth_mode(),
        "proxy_secret": "<already set on server>" if proxy_secret_configured else "<paste value here>",
        "proxy_secret_configured": proxy_secret_configured,
        "proxy_secret_strong": proxy_secret_strong,
        "tenant_id": env_config["tenant_id"],
        "client_id": env_config["client_id"],
        "client_secret": secret_placeholder("GATEWATCH_ENTRA_CLIENT_SECRET"),
        "client_secret_configured": bool(env_config["client_secret"]),
        "redirect_uri": env_config["redirect_uri"],
        "admin_group": admin_group,
        "supervisor_group": supervisor_group,
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
            "supervisorGroupCanonical": supervisor_group,
            "authMode": auth_mode(),
            "tenantId": env_config["tenant_id"],
            "clientId": env_config["client_id"],
            "redirectUri": env_config["redirect_uri"],
            "allowInsecureNetwork": allow_insecure_network(),
        },
        "configFile": config_file_status(),
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
            "proxySecret": {
                "configured": proxy_secret_configured,
                "strong": proxy_secret_strong,
                "message": "Trusted proxy shared secret is configured."
                if proxy_secret_configured and proxy_secret_strong
                else f"Trusted proxy shared secret must be at least {PROXY_SECRET_MIN_LENGTH} characters.",
            },
        },
        "checks": config_checks(config),
        "envTemplate": build_env_template(config),
        "saveStatus": {
            "saved": False,
            "verified": False,
            "restartRequired": False,
            "message": f"Configuration saves to {runtime_config_file()}.",
        },
    }


def admin_config_preview(payload: dict) -> dict:
    config, _ = admin_config_from_payload(payload)
    return {
        "checks": config_checks(config),
        "envTemplate": build_env_template(config),
        "configFile": config_file_status(),
        "secrets": {
            "sessionSecret": {"configured": config["session_secret_configured"]},
            "entraClientSecret": {"configured": config["client_secret_configured"]},
            "proxySecret": {
                "configured": config["proxy_secret_configured"],
                "strong": config["proxy_secret_strong"],
            },
        },
        "saveStatus": {
            "saved": False,
            "verified": False,
            "restartRequired": False,
            "message": "Preview only. Save to upload this configuration to the server env file.",
        },
    }


def write_runtime_config_file(path: Path, values: dict[str, str]) -> None:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    lines = [env_template_line(key, values.get(key, "")) for key in ("GATEWATCH_CONFIG_FILE", *RUNTIME_CONFIG_KEYS)]
    try:
        temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(temp_path, 0o660)
        except OSError:
            pass
        os.replace(temp_path, target)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ApiError(500, f"Could not write Gatewatch configuration file: {exc}") from exc


def verify_runtime_config_file(path: Path, values: dict[str, str]) -> None:
    saved = read_env_file(path)
    mismatched = [
        key
        for key in ("GATEWATCH_CONFIG_FILE", *RUNTIME_CONFIG_KEYS)
        if saved.get(key, "") != values.get(key, "")
    ]
    if mismatched:
        raise ApiError(500, f"Saved configuration verification failed for {', '.join(mismatched)}")


def apply_runtime_config(values: dict[str, str]) -> None:
    for key in RUNTIME_CONFIG_KEYS:
        os.environ[key] = values.get(key, "")
    os.environ["GATEWATCH_CONFIG_FILE"] = values.get("GATEWATCH_CONFIG_FILE", str(runtime_config_file()))


def admin_config_save(payload: dict) -> dict:
    config, env_values = admin_config_from_payload(payload)
    blocked = [check["label"] for check in config_checks(config) if check.get("blocked")]
    if blocked:
        raise ApiError(400, f"Configuration has blocked checks: {', '.join(blocked)}")
    before = {key: os.environ.get(key, "").strip() for key in RUNTIME_CONFIG_KEYS}
    target = runtime_config_file()
    write_runtime_config_file(target, env_values)
    verify_runtime_config_file(target, env_values)
    apply_runtime_config(env_values)
    saved = admin_config_payload()
    restart_keys = [
        key
        for key in ("GATEWATCH_HOST", "GATEWATCH_PORT", "GATEWATCH_DB", "GATEWATCH_SESSION_SECRET")
        if before.get(key, "") != env_values.get(key, "")
    ]
    saved["saveStatus"] = {
        "saved": True,
        "verified": True,
        "restartRequired": bool(restart_keys),
        "restartKeys": restart_keys,
        "message": f"Configuration saved and verified at {target}.",
    }
    return saved


def path_status(path: Path) -> dict:
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=False)
    except OSError:
        resolved = expanded.absolute()
    parent = resolved.parent
    exists = resolved.exists()
    try:
        size_bytes = resolved.stat().st_size if exists else 0
    except OSError:
        size_bytes = 0
    return {
        "path": str(resolved),
        "exists": exists,
        "sizeBytes": size_bytes,
        "parent": str(parent),
        "parentExists": parent.exists(),
        "parentWritable": parent.exists() and os.access(parent, os.W_OK),
    }


def admin_diagnostics_payload(store: "Store", headers) -> dict:
    env_config = entra_config()
    host = os.environ.get("GATEWATCH_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("GATEWATCH_PORT", "8087").strip() or "8087"
    config = admin_config_payload()
    session = current_session(headers)
    return {
        "generatedAt": utc_now(),
        "health": store.health(),
        "runtime": {
            "service": "gatewatch",
            "serverVersion": "Gatewatch/2.0",
            "pythonVersion": platform.python_version(),
            "platform": platform.platform(),
            "processId": os.getpid(),
            "workingDirectory": str(Path.cwd()),
            "baseDirectory": str(BASE_DIR),
            "staticDirectory": str(STATIC_DIR),
        },
        "network": {
            "host": host,
            "port": port,
            "isLoopback": is_loopback_bind(host),
            "allowInsecureNetwork": allow_insecure_network(),
        },
        "auth": {
            "configured": env_config["configured"],
            "ssoConfigured": env_config["sso_configured"],
            "graphConfigured": env_config["graph_configured"],
            "sessionPersistent": env_config["session_persistent"],
            "adminGroup": admin_group_canonical(),
            "supervisorGroup": supervisor_group_canonical(),
            "signedInUser": session,
            "permissions": auth_permissions_payload(headers),
        },
        "storage": path_status(store.db_path),
        "database": store.database_diagnostics(),
        "checks": config["checks"],
        "recentAudit": store.audit_log(),
        "recentChangeRequests": store.list_change_requests("all"),
    }


def default_update_data_dir() -> str:
    configured = os.environ.get("GATEWATCH_UPDATE_DATA_DIR", "").strip()
    if configured:
        return configured
    database_path = os.environ.get("GATEWATCH_DB", str(DEFAULT_DB_PATH)).strip() or str(DEFAULT_DB_PATH)
    return str(Path(database_path).expanduser().parent)


def default_update_mode() -> str:
    configured = os.environ.get("GATEWATCH_UPDATE_MODE", "").strip().lower()
    if configured in UPDATE_MODES:
        return configured
    data_dir = default_update_data_dir()
    if Path("/.dockerenv").exists() or data_dir == "/data" or data_dir.startswith("/data/"):
        return "volume"
    return "systemd" if os.name != "nt" else "auto"


def default_update_script_path() -> Path:
    configured = os.environ.get("GATEWATCH_UPDATE_SCRIPT", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "scripts" / "update_gatewatch.py"


def default_update_status_file(data_dir: str | None = None) -> str:
    configured = os.environ.get("GATEWATCH_UPDATE_STATUS_FILE", "").strip()
    if configured:
        return configured
    return str(Path(data_dir or default_update_data_dir()) / "gatewatch-update-status.json")


def default_update_log_file(data_dir: str | None = None) -> str:
    configured = os.environ.get("GATEWATCH_UPDATE_LOG_FILE", "").strip()
    if configured:
        return configured
    return str(Path(data_dir or default_update_data_dir()) / "gatewatch-update.log")


def update_source_url_for_branch(branch: str) -> str:
    return f"https://github.com/skellywix/Gatewatch/archive/refs/heads/{branch}.tar.gz"


def validate_update_branch(branch: str) -> str:
    text = normalize_text(branch or DEFAULT_UPDATE_BRANCH, "Update branch", maximum=120)
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", text):
        raise ApiError(400, "Update branch can only contain letters, numbers, slash, dot, underscore, and hyphen")
    if text.startswith(("-", "/", ".")) or text.endswith("/") or ".." in text:
        raise ApiError(400, "Update branch must be a normal GitHub branch name")
    return text


def validate_update_source_url(source_url: str, branch: str) -> str:
    text = normalize_text(source_url or update_source_url_for_branch(branch), "Update source URL", maximum=500)
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ApiError(400, "Update source URL must be an HTTPS github.com URL")
    expected_prefix = "/skellywix/Gatewatch/archive/refs/heads/"
    if not parsed.path.startswith(expected_prefix) or not parsed.path.endswith(".tar.gz"):
        raise ApiError(400, "Update source URL must point to the skellywix/Gatewatch branch archive")
    expected_path = f"{expected_prefix}{branch}.tar.gz"
    if parsed.path != expected_path:
        raise ApiError(400, "Update source URL must match the selected GitHub branch")
    if any(char in text for char in "\r\n\t"):
        raise ApiError(400, "Update source URL contains unsupported whitespace")
    return text


def normalize_update_path(value, label: str, fallback: str) -> str:
    text = normalize_text(value or fallback, label, maximum=500)
    if any(char in text for char in "\r\n\t"):
        raise ApiError(400, f"{label} cannot contain control whitespace")
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    if ".." in parts:
        raise ApiError(400, f"{label} cannot contain parent directory segments")
    normalized = text.replace("\\", "/")
    if normalized == "/" or re.fullmatch(r"[A-Za-z]:/?", normalized):
        raise ApiError(400, f"{label} cannot be a filesystem root")
    expanded = Path(text).expanduser()
    if not expanded.is_absolute() and not text.startswith("/"):
        raise ApiError(400, f"{label} must be an absolute path")
    return text


def path_within_directory(path: Path, directory: Path) -> bool:
    try:
        path.expanduser().resolve(strict=False).relative_to(directory.expanduser().resolve(strict=False))
        return True
    except ValueError:
        return False


def update_database_check(data_dir: str) -> dict:
    data_path = Path(data_dir)
    configured_db = os.environ.get("GATEWATCH_DB", str(DEFAULT_DB_PATH)).strip() or str(DEFAULT_DB_PATH)
    db_path = Path(configured_db)
    inside_data_dir = path_within_directory(db_path, data_path)
    return {
        "key": "sqliteData",
        "label": "SQLite data",
        "status": "ok" if inside_data_dir else "blocked",
        "blocked": not inside_data_dir,
        "message": f"SQLite database {db_path.expanduser().resolve(strict=False)} is inside the persistent update directory."
        if inside_data_dir
        else f"Set Update data directory to contain the configured SQLite database ({db_path.expanduser().resolve(strict=False)}) before updating.",
    }


def admin_update_config_from_payload(payload: dict | None = None) -> dict:
    payload = payload or {}
    mode = normalize_text(payload.get("updateMode") or default_update_mode(), "Update mode", maximum=20).lower()
    if mode not in UPDATE_MODES:
        raise ApiError(400, "Update mode must be auto, volume, or systemd")
    branch = validate_update_branch(payload.get("updateBranch") or os.environ.get("GATEWATCH_UPDATE_BRANCH") or DEFAULT_UPDATE_BRANCH)
    source_url = validate_update_source_url(
        payload.get("updateSourceUrl") or os.environ.get("GATEWATCH_UPDATE_SOURCE_URL") or update_source_url_for_branch(branch),
        branch,
    )
    data_dir = normalize_update_path(payload.get("updateDataDir"), "Update data directory", default_update_data_dir())
    config = {
        "updateMode": mode,
        "updateBranch": branch,
        "updateSourceUrl": source_url,
        "updateDataDir": data_dir,
        "updateInstallDir": normalize_update_path(
            payload.get("updateInstallDir"),
            "Update install directory",
            os.environ.get("GATEWATCH_UPDATE_INSTALL_DIR", "/opt/gatewatch"),
        ),
        "updateServiceName": normalize_text(
            payload.get("updateServiceName") or os.environ.get("GATEWATCH_UPDATE_SERVICE_NAME") or "gatewatch",
            "Update service name",
            maximum=120,
        ),
        "updateStatusFile": normalize_update_path(
            payload.get("updateStatusFile"),
            "Update status file",
            default_update_status_file(data_dir),
        ),
        "updateLogFile": normalize_update_path(
            payload.get("updateLogFile"),
            "Update log file",
            default_update_log_file(data_dir),
        ),
        "restartAfterUpdate": bool(payload.get("restartAfterUpdate", True)),
    }
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", config["updateServiceName"]):
        raise ApiError(400, "Update service name may only contain letters, numbers, underscore, dot, @, and hyphen")
    return config


def read_update_status_file(path: str) -> dict:
    target = Path(path).expanduser()
    if not target.exists():
        return {
            "state": "idle",
            "message": "No update has been started from this app yet.",
            "statusFile": str(target),
        }
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "unknown",
            "message": "Update status file could not be read.",
            "statusFile": str(target),
        }
    if not isinstance(payload, dict):
        return {
            "state": "unknown",
            "message": "Update status file had an unexpected shape.",
            "statusFile": str(target),
        }
    payload.setdefault("statusFile", str(target))
    return payload


def write_update_status_file(path: str, payload: dict) -> None:
    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        raise ApiError(500, f"Could not write update status file: {exc}") from exc


def read_update_log_tail(path: str, limit: int = 12000) -> str:
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        return ""
    try:
        with target.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def split_update_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ApiError(500, f"GATEWATCH_UPDATE_COMMAND is not valid: {exc}") from exc
    normalized = [part.strip().strip('"').strip("'") for part in parts if part.strip()]
    if not normalized:
        raise ApiError(500, "GATEWATCH_UPDATE_COMMAND is empty")
    return normalized


def update_command(config: dict) -> list[str]:
    configured = os.environ.get("GATEWATCH_UPDATE_COMMAND", "").strip()
    if configured:
        command = split_update_command(configured)
    else:
        script_path = default_update_script_path()
        command = [sys.executable, str(script_path)]
    command.extend(
        [
            "--yes",
            "--mode",
            config["updateMode"],
            "--branch",
            config["updateBranch"],
            "--source-url",
            config["updateSourceUrl"],
            "--data-dir",
            config["updateDataDir"],
            "--install-dir",
            config["updateInstallDir"],
            "--service-name",
            config["updateServiceName"],
            "--status-file",
            config["updateStatusFile"],
            "--log-file",
            config["updateLogFile"],
        ]
    )
    if config["restartAfterUpdate"]:
        command.append("--restart-process")
    return command


def track_background_update(process: subprocess.Popen) -> None:
    BACKGROUND_UPDATE_PROCESSES[:] = [item for item in BACKGROUND_UPDATE_PROCESSES if item.poll() is None]
    BACKGROUND_UPDATE_PROCESSES.append(process)


def update_checks(config: dict) -> list[dict]:
    script_path = default_update_script_path()
    command_configured = bool(os.environ.get("GATEWATCH_UPDATE_COMMAND", "").strip())
    data_status = path_status(Path(config["updateDataDir"]))
    status_file = path_status(Path(config["updateStatusFile"]))
    log_file = path_status(Path(config["updateLogFile"]))
    script_ok = command_configured or script_path.exists()
    checks = [
        {
            "key": "source",
            "label": "GitHub source",
            "status": "ok",
            "blocked": False,
            "message": "Update source is the Gatewatch GitHub branch archive.",
        },
        {
            "key": "script",
            "label": "Updater command",
            "status": "ok" if script_ok else "blocked",
            "blocked": not script_ok,
            "message": "Updater command is configured."
            if command_configured
            else f"Updater script found at {script_path}."
            if script_ok
            else f"Updater script is missing at {script_path}.",
        },
        {
            "key": "dataDir",
            "label": "Persistent data",
            "status": "ok" if data_status["parentExists"] else "warning",
            "blocked": False,
            "message": f"Updates preserve SQLite data and logs under {data_status['path']}.",
        },
        update_database_check(config["updateDataDir"]),
        {
            "key": "statusFile",
            "label": "Update status log",
            "status": "ok" if status_file["parentExists"] or data_status["exists"] else "warning",
            "blocked": False,
            "message": f"Status will be written to {status_file['path']}.",
        },
        {
            "key": "logFile",
            "label": "Update output log",
            "status": "ok" if log_file["parentExists"] or data_status["exists"] else "warning",
            "blocked": False,
            "message": f"Output will append to {log_file['path']}.",
        },
    ]
    if config["updateMode"] == "systemd" and not command_configured and os.name != "nt" and os.geteuid() != 0:
        checks.append(
            {
                "key": "privilege",
                "label": "Systemd privileges",
                "status": "warning",
                "blocked": False,
                "message": "Systemd updates usually need GATEWATCH_UPDATE_COMMAND configured with a narrow sudo wrapper.",
            }
        )
    return checks


def admin_update_payload(payload: dict | None = None) -> dict:
    config = admin_update_config_from_payload(payload)
    status = read_update_status_file(config["updateStatusFile"])
    return {
        "config": config,
        "checks": update_checks(config),
        "status": status,
        "logTail": read_update_log_tail(config["updateLogFile"]),
    }


def start_admin_update(payload: dict, actor: str) -> dict:
    update_payload = admin_update_payload(payload)
    config = update_payload["config"]
    blocked = [check["label"] for check in update_payload["checks"] if check.get("blocked")]
    if blocked:
        raise ApiError(400, f"Update configuration has blocked checks: {', '.join(blocked)}")
    current = update_payload["status"]
    if current.get("state") == "running":
        raise ApiError(409, "A Gatewatch update is already running")

    command = update_command(config)
    started = utc_now()
    status = {
        "state": "running",
        "message": "Gatewatch update requested from the admin console.",
        "startedAt": started,
        "updatedAt": started,
        "requestedBy": actor,
        "branch": config["updateBranch"],
        "sourceUrl": config["updateSourceUrl"],
        "mode": config["updateMode"],
        "dataDir": config["updateDataDir"],
        "statusFile": config["updateStatusFile"],
        "logFile": config["updateLogFile"],
    }
    write_update_status_file(config["updateStatusFile"], status)
    log_target = Path(config["updateLogFile"]).expanduser()
    try:
        log_target.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_target.open("a", encoding="utf-8")
    except OSError as exc:
        failed = {**status, "state": "failed", "message": f"Could not open update log file: {exc}", "updatedAt": utc_now()}
        write_update_status_file(config["updateStatusFile"], failed)
        raise ApiError(500, f"Could not open update log file: {exc}") from exc
    env = os.environ.copy()
    env["GATEWATCH_UPDATE_REQUESTED_BY"] = actor
    env["GATEWATCH_UPDATE_STATUS_FILE"] = config["updateStatusFile"]
    env["GATEWATCH_UPDATE_LOG_FILE"] = config["updateLogFile"]
    env["GATEWATCH_UPDATE_PARENT_PID"] = str(os.getpid())
    try:
        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=os.name != "nt",
        )
        track_background_update(process)
    except OSError as exc:
        log_handle.close()
        failed = {**status, "state": "failed", "message": f"Updater could not start: {exc}", "updatedAt": utc_now()}
        write_update_status_file(config["updateStatusFile"], failed)
        raise ApiError(500, failed["message"]) from exc
    log_handle.close()
    return admin_update_payload(config)


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


def _fetch_graph_collection(url: str, headers: dict, max_pages: int, payload_name: str) -> list[dict]:
    items: list[dict] = []
    for _ in range(max_pages):
        payload = http_get_json(url, headers)
        page = payload.get("value", [])
        if not isinstance(page, list):
            raise ApiError(502, f"Microsoft Graph returned invalid {payload_name} payload")
        items.extend([item for item in page if isinstance(item, dict)])
        next_link = payload.get("@odata.nextLink")
        if not next_link:
            return items
        url = str(next_link)
    raise ApiError(502, f"Microsoft Graph {payload_name} payload exceeded the configured page limit")


def graph_page_limit_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ApiError(502, f"{name} must be a positive integer") from exc
    if limit < 1:
        raise ApiError(502, f"{name} must be a positive integer")
    return limit


def fetch_graph_me_groups(access_token: str) -> list[dict]:
    query = urlencode({"$select": ENTRA_GROUP_SELECT, "$top": "999"})
    url = f"https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.group?{query}"
    max_pages = graph_page_limit_env("GATEWATCH_ENTRA_MAX_GROUP_PAGES", 10)
    return _fetch_graph_collection(
        url,
        {
            "Authorization": f"Bearer {access_token}",
            "ConsistencyLevel": "eventual",
        },
        max_pages,
        "group membership",
    )


def resolve_session_authorization(access_token: str) -> dict:
    checked_at = utc_now()
    try:
        groups = fetch_graph_me_groups(access_token)
    except ApiError as exc:
        return {
            "can_modify_employees": False,
            "can_delete_employees": False,
            "can_administer_system": False,
            "can_manage_templates": False,
            "role": "user",
            "admin_group": admin_group_canonical(),
            "supervisor_group": supervisor_group_canonical(),
            "groups_checked_at": checked_at,
            "group_check_error": exc.message,
        }
    can_administer = any(group_matches_admin(group) for group in groups)
    can_modify = can_administer or any(group_matches_supervisor(group) for group in groups)
    return {
        "can_modify_employees": can_modify,
        "can_delete_employees": can_administer,
        "can_administer_system": can_administer,
        "can_manage_templates": can_modify,
        "role": permission_role(can_administer=can_administer, can_modify=can_modify),
        "admin_group": admin_group_canonical(),
        "supervisor_group": supervisor_group_canonical(),
        "groups_checked_at": checked_at,
        "group_check_error": "",
    }


def fetch_graph_users() -> list[dict]:
    config = entra_config()
    if not config["graph_configured"]:
        raise ApiError(503, "Microsoft Entra ID Graph sync is not configured")
    max_pages = graph_page_limit_env("GATEWATCH_ENTRA_MAX_GRAPH_PAGES", 20)
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
    return _fetch_graph_collection(url, {"Authorization": f"Bearer {access_token}"}, max_pages, "users")


def graph_user_to_employee(user: dict) -> dict:
    entra_id = normalize_text(user.get("id"), "Entra ID", required=True, maximum=160)
    upn = normalize_text(user.get("userPrincipalName"), "User principal name", maximum=254).lower()
    email_value = user.get("mail") or upn
    email = normalize_email(email_value, required=True)
    employee_id = normalize_text(user.get("employeeId") or upn or entra_id, "Employee ID", required=True, maximum=80)
    business_phones = user.get("businessPhones") if isinstance(user.get("businessPhones"), list) else []
    phone_value = user.get("mobilePhone") or next((phone for phone in business_phones if phone), "")
    account_enabled = user.get("accountEnabled")
    status = "disabled" if account_enabled is False else "active"
    return {
        "employee_id": employee_id,
        "name": normalize_text(user.get("displayName") or upn or email, "Name", required=True, maximum=160),
        "email": email,
        "phone": normalize_text(phone_value, "Phone", maximum=80),
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
                    phone TEXT NOT NULL DEFAULT '',
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
                    access_profile_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL DEFAULT '',
                    deleted_by TEXT NOT NULL DEFAULT '',
                    deleted_reason TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL DEFAULT '',
                    decision_note TEXT NOT NULL DEFAULT '',
                    applied_after_json TEXT
                );

                CREATE TABLE IF NOT EXISTS access_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    section TEXT NOT NULL,
                    field_type TEXT NOT NULL DEFAULT 'text' CHECK (field_type IN ('text', 'textarea', 'checkbox', 'date', 'select')),
                    options_json TEXT NOT NULL DEFAULT '[]',
                    required INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    access_profile_json TEXT NOT NULL DEFAULT '{}',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name);
                CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status);
                CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status);
                CREATE INDEX IF NOT EXISTS idx_change_requests_employee_id ON change_requests(employee_id);
                CREATE INDEX IF NOT EXISTS idx_change_requests_requested_at ON change_requests(requested_at);
                CREATE INDEX IF NOT EXISTS idx_access_fields_active_sort ON access_fields(active, sort_order, label);
                CREATE INDEX IF NOT EXISTS idx_access_templates_active_name ON access_templates(active, lower(name), id);
                """
            )
            self._migrate_employee_status_check(conn)
            self._migrate_employee_columns(conn)
            self._ensure_employee_indexes(conn)
            self._ensure_access_fields_seeded(conn)

    def _ensure_employee_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employees_deleted_at ON employees(deleted_at)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_entra_id ON employees(entra_id) WHERE entra_id != ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_employee_id ON change_requests(employee_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_requests_requested_at ON change_requests(requested_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_fields_active_sort ON access_fields(active, sort_order, label)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_templates_active_name ON access_templates(active, lower(name), id)")

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
                phone TEXT NOT NULL DEFAULT '',
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
                access_profile_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT '',
                deleted_by TEXT NOT NULL DEFAULT '',
                deleted_reason TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
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
            "phone": "TEXT NOT NULL DEFAULT ''",
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
            "access_profile_json": "TEXT NOT NULL DEFAULT '{}'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "deleted_at": "TEXT NOT NULL DEFAULT ''",
            "deleted_by": "TEXT NOT NULL DEFAULT ''",
            "deleted_reason": "TEXT NOT NULL DEFAULT ''",
            "created_by": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE employees ADD COLUMN {quote_identifier(column)} {definition}")

    def _ensure_access_fields_seeded(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM access_fields").fetchone()[0]
        if count:
            return
        now = utc_now()
        for item in DEFAULT_ACCESS_FIELDS:
            conn.execute(
                """
                INSERT INTO access_fields (
                    key, label, section, field_type, options_json, required,
                    active, sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                [
                    item["key"],
                    item["label"],
                    item["section"],
                    item["field_type"],
                    json.dumps(item["options"], separators=(",", ":"), sort_keys=True),
                    item["required"],
                    item["sort_order"],
                    now,
                    now,
                ],
            )

    def _employee_from_row(self, row: sqlite3.Row | None) -> dict | None:
        employee = row_to_dict(row)
        if not employee:
            return None
        raw_profile = employee.pop("access_profile_json", "{}") or "{}"
        try:
            parsed = json.loads(raw_profile)
        except json.JSONDecodeError:
            parsed = {}
        employee["access_profile"] = parsed if isinstance(parsed, dict) else {}
        employee["deleted"] = bool(employee.get("deleted_at"))
        return employee

    def _employee_storage_from_row(self, row: sqlite3.Row | None) -> dict | None:
        employee = row_to_dict(row)
        if not employee:
            return None
        try:
            parsed = json.loads(employee.get("access_profile_json") or "{}")
        except json.JSONDecodeError:
            parsed = {}
        employee["access_profile_json"] = access_profile_json(parsed if isinstance(parsed, dict) else {})
        return employee

    def _access_field_from_row(self, row: sqlite3.Row | None) -> dict | None:
        field = row_to_dict(row)
        if not field:
            return None
        try:
            options = json.loads(field.pop("options_json", "[]") or "[]")
        except json.JSONDecodeError:
            options = []
        field["options"] = options if isinstance(options, list) else []
        field["required"] = bool(field["required"])
        field["active"] = bool(field["active"])
        return field

    def _access_template_from_row(self, row: sqlite3.Row | None) -> dict | None:
        template = row_to_dict(row)
        if not template:
            return None
        raw_profile = template.pop("access_profile_json", "{}") or "{}"
        try:
            parsed = json.loads(raw_profile)
        except json.JSONDecodeError:
            parsed = {}
        template["access_profile"] = parsed if isinstance(parsed, dict) else {}
        template["active"] = bool(template["active"])
        return template

    def template_payload(self, payload: dict, *, partial: bool = False) -> dict:
        data = {}
        if "name" in payload or not partial:
            data["name"] = normalize_text(payload.get("name"), "Template name", required=True, maximum=120)
        if "description" in payload or not partial:
            data["description"] = normalize_text(payload.get("description"), "Template description", maximum=500)
        if "access_profile" in payload or "accessProfile" in payload or not partial:
            profile = normalize_access_profile(payload.get("access_profile", payload.get("accessProfile")))
            if not partial and not any(bool(value) for value in profile.values()):
                raise ApiError(400, "Template must include at least one access value")
            data["access_profile_json"] = json.dumps(profile, separators=(",", ":"), sort_keys=True)
        return data

    def access_field_payload(self, payload: dict, *, partial: bool = False) -> dict:
        data = {}
        if "label" in payload or not partial:
            data["label"] = normalize_text(payload.get("label"), "Field label", required=True, maximum=120)
        if "section" in payload or not partial:
            data["section"] = normalize_text(payload.get("section"), "Field section", required=True, maximum=120)
        if "field_type" in payload or "fieldType" in payload or not partial:
            data["field_type"] = normalize_access_field_type(payload.get("field_type", payload.get("fieldType")))
        if "options" in payload or not partial:
            data["options_json"] = json.dumps(
                normalize_options(payload.get("options")),
                separators=(",", ":"),
                sort_keys=True,
            )
        if "required" in payload or not partial:
            data["required"] = normalize_bool_int(payload.get("required"))
        if "active" in payload or not partial:
            data["active"] = normalize_bool_int(True if "active" not in payload else payload.get("active"))
        if "sort_order" in payload or "sortOrder" in payload or not partial:
            raw_order = payload.get("sort_order", payload.get("sortOrder", 0))
            try:
                sort_order = int(raw_order or 0)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "Sort order must be a number") from exc
            if sort_order < 0 or sort_order > 9999:
                raise ApiError(400, "Sort order must be between 0 and 9999")
            data["sort_order"] = sort_order
        if "key" in payload:
            key = normalize_text(payload.get("key"), "Field key", required=True, maximum=64)
        elif not partial:
            key = slugify_access_key(data.get("label", "field"))
        else:
            key = ""
        if key:
            if not ACCESS_FIELD_KEY_RE.match(key):
                raise ApiError(400, "Field key must use lowercase letters, numbers, and underscores")
            data["key"] = key
        return data

    def list_access_fields(self, *, include_inactive: bool = True) -> list[dict]:
        where = "" if include_inactive else "WHERE active = 1"
        with self.session() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                  FROM access_fields
                  {where}
                 ORDER BY active DESC, sort_order, lower(section), lower(label), id
                """
            ).fetchall()
        return [self._access_field_from_row(row) for row in rows]

    def create_access_field(self, payload: dict, actor: str = "Local user") -> dict:
        data = self.access_field_payload(payload)
        now = utc_now()
        data["created_at"] = now
        data["updated_at"] = now
        with self.session() as conn:
            existing = self._access_field_from_row(
                conn.execute("SELECT * FROM access_fields WHERE key = ?", [data["key"]]).fetchone()
            )
            if existing and not existing["active"]:
                restore = {key: value for key, value in data.items() if key != "created_at"}
                assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in restore)
                conn.execute(
                    f"UPDATE access_fields SET {assignments} WHERE id = :id",
                    {**restore, "id": existing["id"]},
                )
                restored = self._access_field_from_row(
                    conn.execute("SELECT * FROM access_fields WHERE id = ?", [existing["id"]]).fetchone()
                )
                self._audit(
                    conn,
                    "create_access_field",
                    "access_field",
                    existing["id"],
                    actor,
                    f"Restored access field {restored['label']}.",
                    existing,
                    restored,
                )
                return restored
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO access_fields (
                        key, label, section, field_type, options_json, required,
                        active, sort_order, created_at, updated_at
                    )
                    VALUES (
                        :key, :label, :section, :field_type, :options_json, :required,
                        :active, :sort_order, :created_at, :updated_at
                    )
                    """,
                    data,
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Access field key already exists") from exc
            created = self._access_field_from_row(
                conn.execute("SELECT * FROM access_fields WHERE id = ?", [cursor.lastrowid]).fetchone()
            )
            self._audit(conn, "create_access_field", "access_field", created["id"], actor, f"Created access field {created['label']}.", None, created)
            return created

    def update_access_field(self, field_id: int, payload: dict, actor: str = "Local user") -> dict:
        data = self.access_field_payload(payload, partial=True)
        if not data:
            raise ApiError(400, "No access field values were provided")
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = self._access_field_from_row(conn.execute("SELECT * FROM access_fields WHERE id = ?", [field_id]).fetchone())
            if not before:
                raise ApiError(404, "Access field was not found")
            assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in data)
            try:
                conn.execute(
                    f"UPDATE access_fields SET {assignments} WHERE id = :id",
                    {**data, "id": field_id},
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Access field key already exists") from exc
            after = self._access_field_from_row(conn.execute("SELECT * FROM access_fields WHERE id = ?", [field_id]).fetchone())
            self._audit(conn, "update_access_field", "access_field", field_id, actor, f"Updated access field {after['label']}.", before, after)
            return after

    def delete_access_field(self, field_id: int, actor: str = "Local user") -> dict:
        with self.session() as conn:
            before = self._access_field_from_row(conn.execute("SELECT * FROM access_fields WHERE id = ?", [field_id]).fetchone())
            if not before:
                raise ApiError(404, "Access field was not found")
            now = utc_now()
            conn.execute(
                "UPDATE access_fields SET active = 0, updated_at = ? WHERE id = ?",
                [now, field_id],
            )
            after = self._access_field_from_row(conn.execute("SELECT * FROM access_fields WHERE id = ?", [field_id]).fetchone())
            self._audit(conn, "delete_access_field", "access_field", field_id, actor, f"Removed access field {before['label']}.", before, after)
            return after

    def list_access_templates(self, *, include_inactive: bool = False) -> list[dict]:
        where = "" if include_inactive else "WHERE active = 1"
        with self.session() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                  FROM access_templates
                  {where}
                 ORDER BY active DESC, lower(name), id
                """
            ).fetchall()
        return [self._access_template_from_row(row) for row in rows]

    def create_access_template(self, payload: dict, actor: str = "Local user") -> dict:
        data = self.template_payload(payload)
        now = utc_now()
        data["created_at"] = now
        data["updated_at"] = now
        data["active"] = 1
        with self.session() as conn:
            existing = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE name = ?", [data["name"]]).fetchone()
            )
            if existing and not existing["active"]:
                restore = {key: value for key, value in data.items() if key != "created_at"}
                assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in restore)
                conn.execute(
                    f"UPDATE access_templates SET {assignments} WHERE id = :id",
                    {**restore, "id": existing["id"]},
                )
                restored = self._access_template_from_row(
                    conn.execute("SELECT * FROM access_templates WHERE id = ?", [existing["id"]]).fetchone()
                )
                self._audit(
                    conn,
                    "create_access_template",
                    "access_template",
                    existing["id"],
                    actor,
                    f"Restored access template {restored['name']}.",
                    existing,
                    restored,
                )
                return restored
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO access_templates (
                        name, description, access_profile_json, active, created_at, updated_at
                    )
                    VALUES (
                        :name, :description, :access_profile_json, :active, :created_at, :updated_at
                    )
                    """,
                    data,
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Template name already exists") from exc
            created = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE id = ?", [cursor.lastrowid]).fetchone()
            )
            self._audit(
                conn,
                "create_access_template",
                "access_template",
                created["id"],
                actor,
                f"Created access template {created['name']}.",
                None,
                created,
            )
            return created

    def update_access_template(self, template_id: int, payload: dict, actor: str = "Local user") -> dict:
        data = self.template_payload(payload, partial=True)
        if not data:
            raise ApiError(400, "No template values were provided")
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE id = ?", [template_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Template was not found")
            assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in data)
            try:
                conn.execute(
                    f"UPDATE access_templates SET {assignments} WHERE id = :id",
                    {**data, "id": template_id},
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Template name already exists") from exc
            after = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE id = ?", [template_id]).fetchone()
            )
            self._audit(
                conn,
                "update_access_template",
                "access_template",
                template_id,
                actor,
                f"Updated access template {after['name']}.",
                before,
                after,
            )
            return after

    def delete_access_template(self, template_id: int, actor: str = "Local user") -> dict:
        with self.session() as conn:
            before = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE id = ?", [template_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Template was not found")
            now = utc_now()
            conn.execute(
                "UPDATE access_templates SET active = 0, updated_at = ? WHERE id = ?",
                [now, template_id],
            )
            after = self._access_template_from_row(
                conn.execute("SELECT * FROM access_templates WHERE id = ?", [template_id]).fetchone()
            )
            self._audit(
                conn,
                "delete_access_template",
                "access_template",
                template_id,
                actor,
                f"Removed access template {before['name']}.",
                before,
                after,
            )
            return after

    def employee_payload(self, payload: dict, *, partial: bool = False) -> dict:
        fields = {
            "employee_id": ("Key Fob ID", 80, True),
            "name": ("Name", 160, True),
            "email": ("Email", 254, True),
            "phone": ("Phone", 80, False),
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
        if "access_profile" in payload or "accessProfile" in payload or not partial:
            data["access_profile_json"] = access_profile_json(payload.get("access_profile", payload.get("accessProfile")))
        return data

    def summary(self) -> dict:
        with self.session() as conn:
            one = lambda sql, params=(): conn.execute(sql, params).fetchone()[0]
            today_text = utc_now()[:10]
            return {
                "total": one("SELECT COUNT(*) FROM employees WHERE deleted_at = ''"),
                "active": one("SELECT COUNT(*) FROM employees WHERE deleted_at = '' AND status = 'active'"),
                "disabled": one("SELECT COUNT(*) FROM employees WHERE deleted_at = '' AND status = 'disabled'"),
                "terminated": one("SELECT COUNT(*) FROM employees WHERE deleted_at = '' AND status = 'terminated'"),
                "deleted": one("SELECT COUNT(*) FROM employees WHERE deleted_at != ''"),
                "inProgress": one(
                    """
                    SELECT COUNT(*)
                      FROM employees
                     WHERE deleted_at = ''
                       AND status = 'active'
                       AND employee_notified = 0
                       AND (
                            access_needed != ''
                         OR request_received = 1
                         OR manager_approved = 1
                         OR it_provisioned = 1
                       )
                    """
                ),
                "updatedToday": one(
                    "SELECT COUNT(*) FROM employees WHERE deleted_at = '' AND substr(updated_at, 1, 10) = ?",
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

    def database_diagnostics(self) -> dict:
        with self.session() as conn:
            table_rows = conn.execute(
                """
                SELECT name
                  FROM sqlite_master
                 WHERE type = 'table'
                 ORDER BY name
                """
            ).fetchall()
            tables = [row["name"] for row in table_rows]
            row_counts = {}
            for table in tables:
                row_counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table)}"
                ).fetchone()[0]
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            database_list = rows_to_dicts(conn.execute("PRAGMA database_list").fetchall())
        return {
            "quickCheck": quick_check,
            "journalMode": journal_mode,
            "foreignKeys": bool(foreign_keys),
            "pageCount": page_count,
            "pageSize": page_size,
            "estimatedBytes": page_count * page_size,
            "tables": tables,
            "rowCounts": row_counts,
            "attachedDatabases": database_list,
        }

    def list_employees(self, query: str = "", *, include_deleted: bool = False, only_deleted: bool = False) -> list[dict]:
        search = query.strip().lower()
        deleted_clause = "deleted_at != ''" if only_deleted else "1 = 1" if include_deleted else "deleted_at = ''"
        with self.session() as conn:
            if not search:
                rows = conn.execute(
                    f"""
                    SELECT *
                      FROM employees
                     WHERE {deleted_clause}
                     ORDER BY lower(name), id
                    """
                ).fetchall()
            else:
                like = f"%{search}%"
                rows = conn.execute(
                    f"""
                    SELECT *
                      FROM employees
                     WHERE {deleted_clause}
                       AND (
                            lower(employee_id) LIKE ?
                         OR lower(name) LIKE ?
                         OR lower(email) LIKE ?
                         OR lower(phone) LIKE ?
                         OR lower(department) LIKE ?
                         OR lower(title) LIKE ?
                         OR lower(location) LIKE ?
                         OR lower(manager) LIKE ?
                         OR lower(entra_user_principal_name) LIKE ?
                         OR lower(request_source) LIKE ?
                         OR lower(access_needed) LIKE ?
                         OR lower(access_profile_json) LIKE ?
                       )
                     ORDER BY lower(name), id
                    """,
                    [like, like, like, like, like, like, like, like, like, like, like, like],
                ).fetchall()
            return [self._employee_from_row(row) for row in rows]

    def list_deleted_employees(self, query: str = "") -> list[dict]:
        return self.list_employees(query, only_deleted=True)

    def sync_entra_users(self, users: list[dict], actor: str = "Microsoft Entra ID") -> dict:
        result = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "skippedDeleted": 0,
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
                    if before.get("deleted_at"):
                        result["skipped"] += 1
                        result["skippedDeleted"] += 1
                        if len(result["errors"]) < 5:
                            result["errors"].append(f"{data['email']} is in Deleted Users; restore it before syncing")
                        continue
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
                    after = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [before["id"]]).fetchone())
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
                    "created_by": actor,
                    "created_at": now,
                    "updated_at": now,
                }
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO employees (
                            employee_id, name, email, phone, department, title, location, manager,
                            status, entra_id, entra_user_principal_name, entra_account_enabled,
                            entra_synced_at, request_source, access_needed, request_received,
                            manager_approved, it_provisioned, employee_notified,
                            notes, created_by, created_at, updated_at
                        )
                        VALUES (
                            :employee_id, :name, :email, :phone, :department, :title, :location, :manager,
                            :status, :entra_id, :entra_user_principal_name, :entra_account_enabled,
                            :entra_synced_at, :request_source, :access_needed, :request_received,
                            :manager_approved, :it_provisioned, :employee_notified,
                            :notes, :created_by, :created_at, :updated_at
                        )
                        """,
                        insert_data,
                    )
                except sqlite3.IntegrityError:
                    result["skipped"] += 1
                    if len(result["errors"]) < 5:
                        result["errors"].append(f"Duplicate employee ID or email for {data['email']}")
                    continue
                created = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [cursor.lastrowid]).fetchone())
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

    def get_employee(self, employee_id: int, *, include_deleted: bool = False) -> dict:
        with self.session() as conn:
            if include_deleted:
                employee = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            else:
                employee = self._employee_from_row(
                    conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [employee_id]).fetchone()
                )
        if not employee:
            raise ApiError(404, "Employee was not found")
        return employee

    def _json_from_db(self, value: str | None) -> dict | None:
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ApiError(500, "Stored change request JSON was invalid") from exc
        if parsed is not None and not isinstance(parsed, dict):
            raise ApiError(500, "Stored change request JSON was invalid")
        return parsed

    def _change_request_from_row(self, row: sqlite3.Row | None) -> dict | None:
        request = row_to_dict(row)
        if not request:
            return None
        request["payload"] = self._json_from_db(request.pop("payload_json", None)) or {}
        request["before"] = self._json_from_db(request.pop("before_json", None)) or {}
        request["applied_after"] = self._json_from_db(request.pop("applied_after_json", None))
        for holder in (request["payload"], request["before"], request["applied_after"]):
            if isinstance(holder, dict) and "access_profile_json" in holder:
                try:
                    holder["access_profile"] = json.loads(holder.pop("access_profile_json") or "{}")
                except json.JSONDecodeError:
                    holder["access_profile"] = {}
        return request

    def list_change_requests(self, status: str = "pending", *, requested_by: str | None = None) -> list[dict]:
        selected_status = str(status or "pending").strip().lower()
        if selected_status not in CHANGE_REQUEST_STATUSES and selected_status != "all":
            raise ApiError(400, "Change request status must be pending, approved, rejected, or all")
        clauses = []
        params: list[str] = []
        if selected_status != "all":
            clauses.append("cr.status = ?")
            params.append(selected_status)
        if requested_by is not None:
            clauses.append("cr.requested_by = ?")
            params.append(requested_by)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.session() as conn:
            rows = conn.execute(
                f"""
                SELECT cr.*,
                       e.name AS employee_name,
                       e.email AS employee_email,
                       e.employee_id AS employee_key_fob_id
                  FROM change_requests cr
                  LEFT JOIN employees e ON e.id = cr.employee_id
                  {where}
                 ORDER BY
                       CASE cr.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                       cr.id DESC
                 LIMIT 50
                """,
                params,
            ).fetchall()
        return [self._change_request_from_row(row) for row in rows]

    def create_change_request(self, employee_id: int, payload: dict, actor: str = "Local user") -> dict:
        data = self.employee_payload(payload, partial=True)
        if not data:
            raise ApiError(400, "No employee fields were provided")
        now = utc_now()
        with self.session() as conn:
            before = self._employee_storage_from_row(
                conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [employee_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Employee was not found")
            changed = {
                key: value
                for key, value in data.items()
                if before.get(key) != value
            }
            if not changed:
                raise ApiError(400, "No changed employee fields were provided")
            cursor = conn.execute(
                """
                INSERT INTO change_requests (
                    employee_id, status, requested_by, requested_at, payload_json, before_json
                )
                VALUES (?, 'pending', ?, ?, ?, ?)
                """,
                [
                    employee_id,
                    actor,
                    now,
                    json.dumps(changed, sort_keys=True),
                    json.dumps(before, sort_keys=True),
                ],
            )
            request = self._change_request_from_row(
                conn.execute(
                    """
                    SELECT cr.*,
                           e.name AS employee_name,
                           e.email AS employee_email,
                           e.employee_id AS employee_key_fob_id
                      FROM change_requests cr
                      LEFT JOIN employees e ON e.id = cr.employee_id
                     WHERE cr.id = ?
                    """,
                    [cursor.lastrowid],
                ).fetchone()
            )
            self._audit(
                conn,
                "request_change",
                "change_request",
                request["id"],
                actor,
                f"Requested changes for {before['name']}.",
                before,
                request,
            )
            return request

    def review_change_request(self, request_id: int, *, approve: bool, actor: str, note: str = "") -> dict:
        decision_note = normalize_text(note, "Decision note", maximum=500)
        now = utc_now()
        with self.session() as conn:
            request_row = conn.execute("SELECT * FROM change_requests WHERE id = ?", [request_id]).fetchone()
            request = self._change_request_from_row(request_row)
            if not request:
                raise ApiError(404, "Change request was not found")
            if request["status"] != "pending":
                raise ApiError(409, "Change request has already been reviewed")

            employee = self._employee_storage_from_row(
                conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [request["employee_id"]]).fetchone()
            )
            if not employee:
                conn.execute(
                    """
                    UPDATE change_requests
                       SET status = 'rejected',
                           reviewed_by = ?,
                           reviewed_at = ?,
                           decision_note = ?
                     WHERE id = ?
                    """,
                    [actor, now, decision_note or "Employee no longer exists.", request_id],
                )
                reviewed = self._change_request_from_row(conn.execute("SELECT * FROM change_requests WHERE id = ?", [request_id]).fetchone())
                self._audit(
                    conn,
                    "reject_change_request",
                    "change_request",
                    request_id,
                    actor,
                    "Rejected change request because the employee no longer exists.",
                    request,
                    reviewed,
                )
                return reviewed

            if not approve:
                conn.execute(
                    """
                    UPDATE change_requests
                       SET status = 'rejected',
                           reviewed_by = ?,
                           reviewed_at = ?,
                           decision_note = ?
                     WHERE id = ?
                    """,
                    [actor, now, decision_note, request_id],
                )
                reviewed = self._change_request_from_row(
                    conn.execute(
                        """
                        SELECT cr.*,
                               e.name AS employee_name,
                               e.email AS employee_email,
                               e.employee_id AS employee_key_fob_id
                          FROM change_requests cr
                          LEFT JOIN employees e ON e.id = cr.employee_id
                         WHERE cr.id = ?
                        """,
                        [request_id],
                    ).fetchone()
                )
                self._audit(
                    conn,
                    "reject_change_request",
                    "change_request",
                    request_id,
                    actor,
                    f"Rejected change request for {employee['name']}.",
                    request,
                    reviewed,
                )
                return reviewed

            data = {**request["payload"]}
            if "access_profile" in data:
                data["access_profile_json"] = access_profile_json(data.pop("access_profile"))
            data["updated_at"] = now
            assignments = ", ".join(f"{quote_identifier(key)} = :{key}" for key in data)
            try:
                conn.execute(
                    f"UPDATE employees SET {assignments} WHERE id = :id",
                    {**data, "id": request["employee_id"]},
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Key Fob ID or email already exists") from exc
            after = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [request["employee_id"]]).fetchone())
            conn.execute(
                """
                UPDATE change_requests
                   SET status = 'approved',
                       reviewed_by = ?,
                       reviewed_at = ?,
                       decision_note = ?,
                       applied_after_json = ?
                 WHERE id = ?
                """,
                [actor, now, decision_note, json.dumps(after, sort_keys=True), request_id],
            )
            reviewed = self._change_request_from_row(
                conn.execute(
                    """
                    SELECT cr.*,
                           e.name AS employee_name,
                           e.email AS employee_email,
                           e.employee_id AS employee_key_fob_id
                      FROM change_requests cr
                      LEFT JOIN employees e ON e.id = cr.employee_id
                     WHERE cr.id = ?
                    """,
                    [request_id],
                ).fetchone()
            )
            self._audit(
                conn,
                "approve_change_request",
                "change_request",
                request_id,
                actor,
                f"Approved change request for {after['name']}.",
                request,
                reviewed,
            )
            return reviewed

    def create_employee(self, payload: dict, actor: str = "Local user") -> dict:
        data = self.employee_payload(payload)
        now = utc_now()
        data["created_at"] = now
        data["updated_at"] = now
        data["created_by"] = actor
        with self.session() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO employees (
                        employee_id, name, email, phone, department, title, location, manager,
                        status, request_source, access_needed, request_received,
                        manager_approved, it_provisioned, employee_notified,
                        access_profile_json, notes, created_by, created_at, updated_at
                    )
                    VALUES (
                        :employee_id, :name, :email, :phone, :department, :title, :location, :manager,
                        :status, :request_source, :access_needed, :request_received,
                        :manager_approved, :it_provisioned, :employee_notified,
                        :access_profile_json, :notes, :created_by, :created_at, :updated_at
                    )
                    """,
                    data,
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Key Fob ID or email already exists") from exc
            created = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [cursor.lastrowid]).fetchone())
            self._audit(conn, "create", "employee", created["id"], actor, f"Created employee {created['name']}.", None, created)
            return created

    def update_employee(self, employee_id: int, payload: dict, actor: str = "Local user") -> dict:
        data = self.employee_payload(payload, partial=True)
        if not data:
            raise ApiError(400, "No employee fields were provided")
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = self._employee_from_row(
                conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [employee_id]).fetchone()
            )
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
            after = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [employee_id]).fetchone())
            self._audit(conn, "update", "employee", employee_id, actor, f"Updated employee {after['name']}.", before, after)
            return after

    def delete_employee(self, employee_id: int, actor: str = "Local user") -> dict:
        with self.session() as conn:
            before = self._employee_from_row(
                conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at = ''", [employee_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Employee was not found")
            now = utc_now()
            conn.execute(
                """
                UPDATE change_requests
                   SET status = 'rejected',
                       reviewed_by = ?,
                       reviewed_at = ?,
                       decision_note = 'Employee deleted before request was approved.'
                 WHERE employee_id = ?
                   AND status = 'pending'
                """,
                [actor, now, employee_id],
            )
            conn.execute(
                """
                UPDATE employees
                   SET deleted_at = ?,
                       deleted_by = ?,
                       deleted_reason = 'Deleted from Gatewatch',
                       updated_at = ?
                 WHERE id = ?
                """,
                [now, actor, now, employee_id],
            )
            after = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            self._audit(
                conn,
                "delete",
                "employee",
                employee_id,
                actor,
                f"Moved employee {before['name']} to Deleted Users.",
                before,
                after,
            )
            return after

    def restore_employee(self, employee_id: int, actor: str = "Local user") -> dict:
        with self.session() as conn:
            before = self._employee_from_row(
                conn.execute("SELECT * FROM employees WHERE id = ? AND deleted_at != ''", [employee_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Deleted employee was not found")
            now = utc_now()
            conn.execute(
                """
                UPDATE employees
                   SET deleted_at = '',
                       deleted_by = '',
                       deleted_reason = '',
                       updated_at = ?
                 WHERE id = ?
                """,
                [now, employee_id],
            )
            after = self._employee_from_row(conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone())
            self._audit(conn, "restore", "employee", employee_id, actor, f"Restored employee {after['name']}.", before, after)
            return after

    def _delete_legacy_employee_references(self, conn: sqlite3.Connection, employee_id: int) -> None:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for row in tables:
            table = row["name"]
            if table in {"employees", "audit_log", "change_requests", "sqlite_sequence"}:
                continue
            references = conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})").fetchall()
            employee_columns = [ref["from"] for ref in references if ref["table"] == "employees"]
            for column in employee_columns:
                conn.execute(
                    f"DELETE FROM {quote_identifier(table)} WHERE {quote_identifier(column)} = ?",
                    [employee_id],
                )

    def audit_log(self, *, actor: str | None = None) -> list[dict]:
        with self.session() as conn:
            if actor is None:
                rows = conn.execute(
                    """
                    SELECT *
                      FROM audit_log
                     ORDER BY id DESC
                     LIMIT 50
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                      FROM audit_log
                     WHERE actor = ?
                     ORDER BY id DESC
                     LIMIT 50
                    """,
                    [actor],
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
            safe_args = list(args)
            if safe_args:
                request_line = str(safe_args[0])
                parts = request_line.split(" ")
                if len(parts) >= 3:
                    parts[1] = urlparse(parts[1]).path or "/"
                    safe_args[0] = " ".join(parts)
            sys.stderr.write(
                "%s - - [%s] %s\n"
                % (self.address_string(), self.log_date_time_string(), format % tuple(safe_args))
            )

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

        def _can_modify_employees(self) -> bool:
            session = current_session(self.headers)
            return bool(session and session.get("can_modify_employees"))

        def _can_administer_system(self) -> bool:
            session = current_session(self.headers)
            return bool(session and session.get("can_administer_system"))

        def _require_employee_modify(self) -> None:
            if self._can_modify_employees():
                return
            raise ApiError(
                403,
                f"Only supervisors in {supervisor_group_canonical()} or admins in {admin_group_canonical()} can modify employees or templates",
            )

        def _require_administer_system(self) -> None:
            if self._can_administer_system():
                return
            raise ApiError(
                403,
                f"Only members of {admin_group_canonical()} can delete, sync, view logs, or view admin configuration",
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
            verify_csrf_token(self.headers, current_session(self.headers))

        def _handle_api(self, method: str, path: str, query: dict) -> None:
            self._guard_same_origin_mutation(method)
            actor = self._request_actor()
            if method == "GET" and path == "/api/auth/status":
                self._send_json(auth_status_payload(self.headers))
                return
            if method == "GET" and path == "/api/admin/config":
                self._require_administer_system()
                self._send_json({"config": admin_config_payload()})
                return
            if method == "POST" and path == "/api/admin/config":
                self._require_administer_system()
                self._send_json({"config": admin_config_save(self._read_json())})
                return
            if method == "POST" and path == "/api/admin/config/validate":
                self._require_administer_system()
                self._send_json({"preview": admin_config_preview(self._read_json())})
                return
            if method == "GET" and path == "/api/admin/diagnostics":
                self._require_administer_system()
                self._send_json({"diagnostics": admin_diagnostics_payload(store, self.headers)})
                return
            if method == "GET" and path == "/api/admin/update/status":
                self._require_administer_system()
                self._send_json({"update": admin_update_payload()})
                return
            if method == "POST" and path == "/api/admin/update/validate":
                self._require_administer_system()
                self._send_json({"update": admin_update_payload(self._read_json())})
                return
            if method == "POST" and path == "/api/admin/update/apply":
                self._require_administer_system()
                self._send_json({"update": start_admin_update(self._read_json(), actor)}, 202)
                return
            if method == "GET" and path == "/api/bootstrap":
                can_administer = self._can_administer_system()
                change_request_actor = None if can_administer else actor
                audit_actor = None if can_administer else actor
                self._send_json(
                    {
                        "summary": store.summary(),
                        "employees": store.list_employees(query.get("q", [""])[0]),
                        "deletedEmployees": store.list_deleted_employees(query.get("q", [""])[0]) if can_administer else [],
                        "accessFields": store.list_access_fields(),
                        "accessTemplates": store.list_access_templates(),
                        "changeRequests": store.list_change_requests("pending", requested_by=change_request_actor),
                        "audit": store.audit_log(actor=audit_actor),
                        "auth": auth_status_payload(self.headers)["entra"],
                    }
                )
                return
            if method == "POST" and path == "/api/entra/sync":
                self._require_administer_system()
                users = fetch_graph_users()
                self._send_json({"sync": store.sync_entra_users(users, actor=actor)})
                return
            if method == "GET" and path == "/api/employees":
                self._send_json({"employees": store.list_employees(query.get("q", [""])[0])})
                return
            if method == "GET" and path == "/api/access-fields":
                self._send_json({"accessFields": store.list_access_fields()})
                return
            if method == "POST" and path == "/api/access-fields":
                self._require_administer_system()
                self._send_json({"accessField": store.create_access_field(self._read_json(), actor=actor)}, 201)
                return
            if method == "PATCH" and path.startswith("/api/access-fields/"):
                self._require_administer_system()
                field_id = self._path_int(path, "/api/access-fields/", "access field ID")
                self._send_json({"accessField": store.update_access_field(field_id, self._read_json(), actor=actor)})
                return
            if method == "DELETE" and path.startswith("/api/access-fields/"):
                self._require_administer_system()
                field_id = self._path_int(path, "/api/access-fields/", "access field ID")
                self._send_json({"accessField": store.delete_access_field(field_id, actor=actor)})
                return
            if method == "GET" and path == "/api/access-templates":
                self._send_json({"accessTemplates": store.list_access_templates()})
                return
            if method == "POST" and path == "/api/access-templates":
                self._require_employee_modify()
                self._send_json({"accessTemplate": store.create_access_template(self._read_json(), actor=actor)}, 201)
                return
            if method == "PATCH" and path.startswith("/api/access-templates/"):
                self._require_employee_modify()
                template_id = self._path_int(path, "/api/access-templates/", "template ID")
                self._send_json({"accessTemplate": store.update_access_template(template_id, self._read_json(), actor=actor)})
                return
            if method == "DELETE" and path.startswith("/api/access-templates/"):
                self._require_employee_modify()
                template_id = self._path_int(path, "/api/access-templates/", "template ID")
                self._send_json({"accessTemplate": store.delete_access_template(template_id, actor=actor)})
                return
            if method == "POST" and path == "/api/employees":
                self._send_json({"employee": store.create_employee(self._read_json(), actor=actor)}, 201)
                return
            if method == "GET" and path == "/api/change-requests":
                change_request_actor = None if self._can_administer_system() else actor
                self._send_json(
                    {
                        "changeRequests": store.list_change_requests(
                            query.get("status", ["pending"])[0],
                            requested_by=change_request_actor,
                        )
                    }
                )
                return
            if method == "POST" and path.startswith("/api/change-requests/") and path.endswith("/approve"):
                self._require_administer_system()
                request_id = self._path_int_with_suffix(
                    path,
                    "/api/change-requests/",
                    "/approve",
                    "change request ID",
                )
                payload = self._read_json()
                self._send_json(
                    {
                        "changeRequest": store.review_change_request(
                            request_id,
                            approve=True,
                            actor=actor,
                            note=payload.get("note", ""),
                        )
                    }
                )
                return
            if method == "POST" and path.startswith("/api/change-requests/") and path.endswith("/reject"):
                self._require_administer_system()
                request_id = self._path_int_with_suffix(
                    path,
                    "/api/change-requests/",
                    "/reject",
                    "change request ID",
                )
                payload = self._read_json()
                self._send_json(
                    {
                        "changeRequest": store.review_change_request(
                            request_id,
                            approve=False,
                            actor=actor,
                            note=payload.get("note", ""),
                        )
                    }
                )
                return
            if method == "GET" and path.startswith("/api/employees/"):
                self._send_json({"employee": store.get_employee(self._path_int(path, "/api/employees/"))})
                return
            if method == "POST" and path.startswith("/api/employees/") and path.endswith("/restore"):
                self._require_administer_system()
                employee_id = self._path_int_with_suffix(path, "/api/employees/", "/restore", "employee ID")
                self._send_json({"employee": store.restore_employee(employee_id, actor=actor)})
                return
            if method == "PATCH" and path.startswith("/api/employees/"):
                employee_id = self._path_int(path, "/api/employees/")
                if self._can_modify_employees():
                    self._send_json({"employee": store.update_employee(employee_id, self._read_json(), actor=actor)})
                else:
                    self._send_json(
                        {"changeRequest": store.create_change_request(employee_id, self._read_json(), actor=actor)},
                        202,
                    )
                return
            if method == "DELETE" and path.startswith("/api/employees/"):
                self._require_administer_system()
                employee_id = self._path_int(path, "/api/employees/")
                self._send_json({"employee": store.delete_employee(employee_id, actor=actor)})
                return
            if method == "GET" and path == "/api/audit-log":
                self._require_administer_system()
                self._send_json({"audit": store.audit_log()})
                return
            if method == "GET" and path == "/api/audit-log.csv":
                self._require_administer_system()
                self._send_text(store.audit_log_csv(), "text/csv; charset=utf-8")
                return
            raise ApiError(404, "API route not found")

        def _path_int(self, path: str, prefix: str, label: str = "employee ID") -> int:
            value = path.removeprefix(prefix)
            if not value or "/" in value:
                raise ApiError(400, f"Invalid {label}")
            try:
                return int(value)
            except ValueError as exc:
                raise ApiError(400, f"Invalid {label}") from exc

        def _path_int_with_suffix(self, path: str, prefix: str, suffix: str, label: str) -> int:
            if not path.startswith(prefix) or not path.endswith(suffix):
                raise ApiError(400, f"Invalid {label}")
            value = path[len(prefix) : -len(suffix)]
            if not value or "/" in value:
                raise ApiError(400, f"Invalid {label}")
            try:
                return int(value)
            except ValueError as exc:
                raise ApiError(400, f"Invalid {label}") from exc

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
            try:
                raw = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ApiError(400, "Request body must be valid UTF-8 JSON") from exc
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
