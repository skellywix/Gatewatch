from __future__ import annotations

import csv
import io
import ipaddress
import json
import mimetypes
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
DEFAULT_DB_PATH = BASE_DIR / "data" / "gatewatch.db"
MAX_JSON_BODY_BYTES = 1_000_000
EMPLOYEE_STATUSES = {"active", "terminated"}
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
        raise ApiError(400, "Status must be active or terminated")
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
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'terminated')),
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
            self._migrate_employee_columns(conn)

    def _migrate_employee_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(employees)").fetchall()}
        additions = {
            "title": "TEXT NOT NULL DEFAULT ''",
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
            "employee_id": ("Employee ID", 80, True),
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
                        OR lower(request_source) LIKE ?
                        OR lower(access_needed) LIKE ?
                     ORDER BY lower(name), id
                    """,
                    [like, like, like, like, like, like, like, like, like],
                ).fetchall()
            return rows_to_dicts(rows)

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
                raise ApiError(409, "Employee ID or email already exists") from exc
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
                raise ApiError(409, "Employee ID or email already exists") from exc
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

        def _handle_api(self, method: str, path: str, query: dict) -> None:
            actor = self.headers.get("X-Gatewatch-Actor", "Local user").strip() or "Local user"
            if method == "GET" and path == "/api/bootstrap":
                self._send_json(
                    {
                        "summary": store.summary(),
                        "employees": store.list_employees(query.get("q", [""])[0]),
                        "audit": store.audit_log(),
                    }
                )
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
                employee_id = self._path_int(path, "/api/employees/")
                self._send_json({"employee": store.update_employee(employee_id, self._read_json(), actor=actor)})
                return
            if method == "DELETE" and path.startswith("/api/employees/"):
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
