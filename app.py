from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
DEFAULT_DB_PATH = BASE_DIR / "data" / "access_register.db"
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return date.today().isoformat()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def require_fields(payload: dict, fields: list[str]) -> None:
    missing = [field for field in fields if not str(payload.get(field, "")).strip()]
    if missing:
        raise ApiError(400, f"Missing required field(s): {', '.join(missing)}")


def normalize_url(value: str | None, field_label: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ApiError(400, f"{field_label} must be a valid http or https URL")
    return text


def parse_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "enabled", "enable", "active"}:
        return True
    if text in {"false", "0", "no", "n", "disabled", "disable", "inactive"}:
        return False
    return None


def csv_safe_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def insert_row(conn: sqlite3.Connection, table: str, data: dict) -> int:
    keys = list(data.keys())
    columns = ", ".join(keys)
    placeholders = ", ".join(["?"] * len(keys))
    values = [data[key] for key in keys]
    cursor = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


def update_row(conn: sqlite3.Connection, table: str, entity_id: int, data: dict) -> None:
    if not data:
        return
    assignments = ", ".join([f"{key} = ?" for key in data])
    values = list(data.values()) + [entity_id]
    conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", values)


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
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

    def init(self, seed: bool = True) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    department TEXT NOT NULL,
                    location TEXT NOT NULL,
                    manager TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active', 'terminated')),
                    start_date TEXT,
                    termination_date TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    ad_object_guid TEXT,
                    ad_sam_account_name TEXT,
                    ad_user_principal_name TEXT,
                    ad_enabled INTEGER,
                    ad_distinguished_name TEXT,
                    ad_last_logon_at TEXT,
                    ad_last_sync_at TEXT,
                    ad_disabled_flagged_at TEXT,
                    admin_override INTEGER NOT NULL DEFAULT 0,
                    admin_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS systems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    product_name TEXT,
                    application_url TEXT,
                    admin_url TEXT,
                    documentation_url TEXT,
                    category TEXT NOT NULL CHECK (category IN ('software', 'physical_location', 'network', 'shared_resource')),
                    owner TEXT NOT NULL,
                    risk_level TEXT NOT NULL CHECK (risk_level IN ('standard', 'privileged', 'critical')),
                    review_frequency_days INTEGER NOT NULL DEFAULT 90,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    system_id INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    access_level TEXT NOT NULL,
                    access_type TEXT NOT NULL CHECK (
                        access_type IN ('user', 'admin', 'building_code', 'badge', 'shared_account', 'vendor', 'service_account')
                    ),
                    status TEXT NOT NULL CHECK (
                        status IN ('requested', 'approved', 'active', 'removal_pending', 'removed', 'unknown')
                    ),
                    business_reason TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    owner TEXT NOT NULL,
                    last_reviewed_at TEXT,
                    removal_due_at TEXT,
                    removed_at TEXT,
                    removal_evidence TEXT,
                    expires_at TEXT,
                    evidence_url TEXT,
                    evidence_notes TEXT,
                    mfa_enabled INTEGER,
                    last_rotated_at TEXT,
                    rotation_due_at TEXT,
                    notes TEXT,
                    source_import_run_id INTEGER REFERENCES import_runs(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('planned', 'open', 'complete')),
                    due_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    system_id INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    source_name TEXT NOT NULL,
                    imported_by TEXT NOT NULL,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    matched_rows INTEGER NOT NULL DEFAULT 0,
                    unmatched_rows INTEGER NOT NULL DEFAULT 0,
                    inactive_employee_rows INTEGER NOT NULL DEFAULT 0,
                    created_access_records INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_run_id INTEGER NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
                    system_account TEXT NOT NULL,
                    display_name TEXT,
                    email TEXT,
                    employee_identifier TEXT,
                    access_level TEXT,
                    access_type TEXT,
                    matched_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
                    status TEXT NOT NULL CHECK (status IN ('matched', 'unmatched', 'inactive_employee')),
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ad_sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    imported_by TEXT NOT NULL,
                    format TEXT NOT NULL,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    created_users INTEGER NOT NULL DEFAULT 0,
                    updated_users INTEGER NOT NULL DEFAULT 0,
                    disabled_users INTEGER NOT NULL DEFAULT 0,
                    reenabled_users INTEGER NOT NULL DEFAULT 0,
                    preserved_overrides INTEGER NOT NULL DEFAULT 0,
                    error_rows INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ad_sync_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    source_name TEXT NOT NULL DEFAULT 'Scheduled Active Directory sync',
                    format TEXT NOT NULL DEFAULT 'csv',
                    directory_text TEXT,
                    interval_hours INTEGER NOT NULL DEFAULT 24,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester TEXT NOT NULL,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    system_id INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    access_level TEXT NOT NULL,
                    access_type TEXT NOT NULL,
                    business_reason TEXT NOT NULL,
                    expiration_date TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'fulfilled')),
                    approver TEXT,
                    decided_at TEXT,
                    decision_notes TEXT,
                    created_access_record_id INTEGER REFERENCES access_records(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    system_id INTEGER REFERENCES systems(id) ON DELETE SET NULL,
                    frequency_days INTEGER NOT NULL DEFAULT 90,
                    due_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('planned', 'open', 'complete')),
                    completed_at TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    severity TEXT NOT NULL CHECK (severity IN ('info', 'medium', 'high', 'critical')),
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'acknowledged', 'sent')),
                    source_type TEXT,
                    source_id INTEGER,
                    due_date TEXT,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT
                );

                CREATE TABLE IF NOT EXISTS connectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    connector_type TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('planned', 'configured', 'needs_credentials', 'disabled')),
                    instructions TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shared_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    system_id INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    account_name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    approved_users TEXT,
                    mfa_enabled INTEGER NOT NULL DEFAULT 0,
                    last_rotated_at TEXT,
                    rotation_due_at TEXT,
                    business_reason TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'rotation_due', 'disabled')),
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS physical_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    system_id INTEGER REFERENCES systems(id) ON DELETE SET NULL,
                    location TEXT NOT NULL,
                    credential_type TEXT NOT NULL CHECK (credential_type IN ('badge', 'key', 'code', 'fob')),
                    credential_identifier TEXT,
                    zone TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active', 'return_pending', 'returned', 'rotated')),
                    issued_at TEXT,
                    due_at TEXT,
                    completed_at TEXT,
                    evidence TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backup_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    backup_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('complete', 'failed')),
                    retention_days INTEGER NOT NULL DEFAULT 90,
                    size_bytes INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    provider TEXT NOT NULL DEFAULT 'local_role_selector',
                    login_required INTEGER NOT NULL DEFAULT 0,
                    admin_group TEXT,
                    reviewer_group TEXT,
                    hr_group TEXT,
                    readonly_group TEXT,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER,
                    summary TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_access_employee ON access_records(employee_id);
                CREATE INDEX IF NOT EXISTS idx_access_system ON access_records(system_id);
                CREATE INDEX IF NOT EXISTS idx_access_status ON access_records(status);
                CREATE INDEX IF NOT EXISTS idx_import_account_status ON import_accounts(status);
                """
            )
            self._migrate(conn)
            if seed:
                count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
                if count == 0:
                    self._seed(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        employee_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(employees)").fetchall()
        }
        additions = {
            "source": "TEXT NOT NULL DEFAULT 'manual'",
            "ad_object_guid": "TEXT",
            "ad_sam_account_name": "TEXT",
            "ad_user_principal_name": "TEXT",
            "ad_enabled": "INTEGER",
            "ad_distinguished_name": "TEXT",
            "ad_last_logon_at": "TEXT",
            "ad_last_sync_at": "TEXT",
            "ad_disabled_flagged_at": "TEXT",
            "admin_override": "INTEGER NOT NULL DEFAULT 0",
            "admin_notes": "TEXT",
        }
        for column, definition in additions.items():
            if column not in employee_columns:
                conn.execute(f"ALTER TABLE employees ADD COLUMN {column} {definition}")
        access_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(access_records)").fetchall()
        }
        access_additions = {
            "expires_at": "TEXT",
            "evidence_url": "TEXT",
            "evidence_notes": "TEXT",
            "mfa_enabled": "INTEGER",
            "last_rotated_at": "TEXT",
            "rotation_due_at": "TEXT",
        }
        for column, definition in access_additions.items():
            if column not in access_columns:
                conn.execute(f"ALTER TABLE access_records ADD COLUMN {column} {definition}")
        system_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(systems)").fetchall()
        }
        system_additions = {
            "product_name": "TEXT",
            "application_url": "TEXT",
            "admin_url": "TEXT",
            "documentation_url": "TEXT",
        }
        for column, definition in system_additions.items():
            if column not in system_columns:
                conn.execute(f"ALTER TABLE systems ADD COLUMN {column} {definition}")
        conn.execute(
            """
            UPDATE systems
               SET product_name = name
             WHERE product_name IS NULL OR trim(product_name) = ''
            """
        )
        now = utc_now()
        conn.execute(
            """
            INSERT OR IGNORE INTO ad_sync_settings (
                id, enabled, source_name, format, directory_text, interval_hours, next_run_at,
                last_run_at, last_status, updated_at
            )
            VALUES (1, 0, 'Scheduled Active Directory sync', 'csv', NULL, 24, NULL, NULL, NULL, ?)
            """,
            [now],
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO auth_settings (
                id, provider, login_required, admin_group, reviewer_group, hr_group, readonly_group, notes, updated_at
            )
            VALUES (1, 'local_role_selector', 0, NULL, NULL, NULL, NULL, 'MVP local role selector is active.', ?)
            """,
            [now],
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_employees_ad_guid ON employees(ad_object_guid);
            CREATE INDEX IF NOT EXISTS idx_employees_ad_enabled ON employees(ad_enabled);
            CREATE INDEX IF NOT EXISTS idx_employees_source ON employees(source);
            CREATE INDEX IF NOT EXISTS idx_access_expires ON access_records(expires_at);
            CREATE INDEX IF NOT EXISTS idx_requests_status ON access_requests(status);
            CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);
            CREATE INDEX IF NOT EXISTS idx_physical_status ON physical_credentials(status);
            """
        )

    def _seed(self, conn: sqlite3.Connection) -> None:
        now = utc_now()
        employee_ids = {}
        for employee in [
            {
                "employee_id": "E-1001",
                "name": "Avery Morgan",
                "email": "avery.morgan@example.local",
                "department": "Operations",
                "location": "HQ",
                "manager": "Dana Chen",
                "status": "active",
                "start_date": "2023-04-10",
                "termination_date": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": "E-1002",
                "name": "Jordan Lee",
                "email": "jordan.lee@example.local",
                "department": "Facilities",
                "location": "Warehouse",
                "manager": "Dana Chen",
                "status": "terminated",
                "start_date": "2022-08-15",
                "termination_date": today(),
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": "E-1003",
                "name": "Priya Shah",
                "email": "priya.shah@example.local",
                "department": "Finance",
                "location": "HQ",
                "manager": "Riley Brooks",
                "status": "active",
                "start_date": "2021-11-01",
                "termination_date": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": "E-1004",
                "name": "Mateo Rivera",
                "email": "mateo.rivera@example.local",
                "department": "IT",
                "location": "Remote",
                "manager": "Sam Patel",
                "status": "active",
                "start_date": "2020-01-07",
                "termination_date": None,
                "created_at": now,
                "updated_at": now,
            },
        ]:
            employee_ids[employee["employee_id"]] = insert_row(conn, "employees", employee)

        system_ids = {}
        for system in [
            {
                "name": "www.example.com Admin Portal",
                "product_name": "Example Admin Portal",
                "application_url": "https://www.example.com/admin",
                "admin_url": "https://www.example.com/admin/users",
                "documentation_url": "https://www.example.com/help/admin",
                "category": "software",
                "owner": "IT Security",
                "risk_level": "critical",
                "review_frequency_days": 60,
                "description": "Customer-facing admin console for company web properties.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Warehouse Building Code",
                "product_name": "Warehouse Building Code",
                "application_url": None,
                "admin_url": None,
                "documentation_url": None,
                "category": "physical_location",
                "owner": "Facilities",
                "risk_level": "privileged",
                "review_frequency_days": 30,
                "description": "Keypad access code for the warehouse entrance.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "HQ Badge System",
                "product_name": "Badge Access Controller",
                "application_url": None,
                "admin_url": None,
                "documentation_url": None,
                "category": "physical_location",
                "owner": "Facilities",
                "risk_level": "privileged",
                "review_frequency_days": 45,
                "description": "Badge access for HQ exterior and interior doors.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Accounting Suite",
                "product_name": "Accounting Suite",
                "application_url": "https://accounting.example.local",
                "admin_url": "https://accounting.example.local/admin",
                "documentation_url": None,
                "category": "software",
                "owner": "Finance Systems",
                "risk_level": "critical",
                "review_frequency_days": 60,
                "description": "Accounting and payment operations system.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Company VPN",
                "product_name": "Company VPN",
                "application_url": "https://vpn.example.local",
                "admin_url": "https://vpn.example.local/admin",
                "documentation_url": None,
                "category": "network",
                "owner": "IT Security",
                "risk_level": "privileged",
                "review_frequency_days": 90,
                "description": "Remote network access for company systems.",
                "created_at": now,
                "updated_at": now,
            },
        ]:
            system_ids[system["name"]] = insert_row(conn, "systems", system)

        stale_date = (date.today() - timedelta(days=120)).isoformat()
        for record in [
            {
                "employee_id": employee_ids["E-1001"],
                "system_id": system_ids["www.example.com Admin Portal"],
                "access_level": "Administrator",
                "access_type": "admin",
                "status": "active",
                "business_reason": "Maintains operational content and emergency account recovery.",
                "approved_by": "Sam Patel",
                "approved_at": "2025-10-15",
                "owner": "IT Security",
                "last_reviewed_at": stale_date,
                "removal_due_at": None,
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": "Original privileged access approval on file.",
                "mfa_enabled": 1,
                "last_rotated_at": None,
                "rotation_due_at": None,
                "notes": "Privileged account requires owner review.",
                "source_import_run_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": employee_ids["E-1001"],
                "system_id": system_ids["HQ Badge System"],
                "access_level": "HQ Exterior + Operations Floor",
                "access_type": "badge",
                "status": "active",
                "business_reason": "Operations coverage at HQ.",
                "approved_by": "Dana Chen",
                "approved_at": "2026-01-08",
                "owner": "Facilities",
                "last_reviewed_at": "2026-05-20",
                "removal_due_at": None,
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": None,
                "mfa_enabled": None,
                "last_rotated_at": None,
                "rotation_due_at": None,
                "notes": None,
                "source_import_run_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": employee_ids["E-1002"],
                "system_id": system_ids["Warehouse Building Code"],
                "access_level": "Door Code",
                "access_type": "building_code",
                "status": "removal_pending",
                "business_reason": "Former warehouse shift lead.",
                "approved_by": "Facilities",
                "approved_at": "2024-05-01",
                "owner": "Facilities",
                "last_reviewed_at": "2026-03-15",
                "removal_due_at": today(),
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": "Building code must be rotated after termination.",
                "mfa_enabled": None,
                "last_rotated_at": "2026-03-01",
                "rotation_due_at": today(),
                "notes": "Code rotation required after termination.",
                "source_import_run_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": employee_ids["E-1003"],
                "system_id": system_ids["Accounting Suite"],
                "access_level": "Approver",
                "access_type": "user",
                "status": "active",
                "business_reason": "Approves month-end accounting workflows.",
                "approved_by": "Riley Brooks",
                "approved_at": "2025-09-01",
                "owner": "Finance Systems",
                "last_reviewed_at": "2026-06-01",
                "removal_due_at": None,
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": None,
                "mfa_enabled": 1,
                "last_rotated_at": None,
                "rotation_due_at": None,
                "notes": None,
                "source_import_run_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "employee_id": employee_ids["E-1004"],
                "system_id": system_ids["Company VPN"],
                "access_level": "Admin",
                "access_type": "admin",
                "status": "active",
                "business_reason": "Maintains VPN profiles and incident response access.",
                "approved_by": "Sam Patel",
                "approved_at": "2024-11-12",
                "owner": "IT Security",
                "last_reviewed_at": "2026-04-20",
                "removal_due_at": None,
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": None,
                "mfa_enabled": 1,
                "last_rotated_at": None,
                "rotation_due_at": None,
                "notes": None,
                "source_import_run_id": None,
                "created_at": now,
                "updated_at": now,
            },
        ]:
            insert_row(conn, "access_records", record)

        import_run_id = insert_row(
            conn,
            "import_runs",
            {
                "system_id": system_ids["www.example.com Admin Portal"],
                "source_name": "Seeded admin export",
                "imported_by": "System",
                "total_rows": 1,
                "matched_rows": 0,
                "unmatched_rows": 1,
                "inactive_employee_rows": 0,
                "created_access_records": 0,
                "created_at": now,
            },
        )
        insert_row(
            conn,
            "import_accounts",
            {
                "import_run_id": import_run_id,
                "system_account": "old.admin",
                "display_name": "Former Admin",
                "email": "old.admin@example.local",
                "employee_identifier": "",
                "access_level": "Administrator",
                "access_type": "admin",
                "matched_employee_id": None,
                "status": "unmatched",
                "raw_json": json.dumps({"account": "old.admin", "role": "Administrator"}),
                "created_at": now,
            },
        )
        self._audit(
            conn,
            actor="System",
            role="Admin",
            action="seed",
            entity_type="database",
            entity_id=None,
            summary="Created starter access inventory data.",
            before=None,
            after={"employees": 4, "systems": 5, "access_records": 5},
        )

    def _audit(
        self,
        conn: sqlite3.Connection,
        actor: str,
        role: str,
        action: str,
        entity_type: str,
        entity_id: int | None,
        summary: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        insert_row(
            conn,
            "audit_log",
            {
                "actor": actor or "Local User",
                "role": role or "Admin",
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "summary": summary,
                "before_json": json.dumps(before, sort_keys=True) if before else None,
                "after_json": json.dumps(after, sort_keys=True) if after else None,
                "created_at": utc_now(),
            },
        )

    def summary(self) -> dict:
        with self.session() as conn:
            one = lambda sql, params=(): conn.execute(sql, params).fetchone()[0]
            active_access = one(
                "SELECT COUNT(*) FROM access_records WHERE status IN ('active', 'approved', 'unknown')"
            )
            privileged_access = one(
                """
                SELECT COUNT(*)
                FROM access_records ar
                JOIN systems s ON s.id = ar.system_id
                WHERE ar.status IN ('active', 'approved', 'unknown', 'removal_pending')
                  AND (ar.access_type = 'admin' OR s.risk_level IN ('privileged', 'critical'))
                """
            )
            stale_reviews = one(
                """
                SELECT COUNT(*)
                FROM access_records ar
                JOIN systems s ON s.id = ar.system_id
                WHERE ar.status IN ('active', 'approved', 'unknown')
                  AND (
                    ar.last_reviewed_at IS NULL
                    OR date(ar.last_reviewed_at) <= date('now', '-' || s.review_frequency_days || ' days')
                  )
                """
            )
            removals_pending = one(
                """
                SELECT COUNT(*)
                FROM access_records ar
                JOIN employees e ON e.id = ar.employee_id
                WHERE ar.status = 'removal_pending'
                   OR (e.status = 'terminated' AND ar.status IN ('active', 'approved', 'unknown'))
                """
            )
            unmatched_imports = one(
                "SELECT COUNT(*) FROM import_accounts WHERE status IN ('unmatched', 'inactive_employee')"
            )
            pending_requests = one("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'")
            expiring_access = one(
                """
                SELECT COUNT(*)
                FROM access_records
                WHERE status IN ('active', 'approved', 'unknown')
                  AND expires_at IS NOT NULL
                  AND date(expires_at) <= date('now', '+14 days')
                """
            )
            overdue_reviews = one(
                """
                SELECT COUNT(*)
                FROM review_campaigns
                WHERE status IN ('planned', 'open')
                  AND date(due_date) < date('now')
                """
            )
            pending_notifications = one("SELECT COUNT(*) FROM notifications WHERE status = 'pending'")
            connector_count = one("SELECT COUNT(*) FROM connectors")
            risk_count = len(self.risk_findings())
            employees = one("SELECT COUNT(*) FROM employees")
            ad_disabled_users = one(
                "SELECT COUNT(*) FROM employees WHERE ad_enabled = 0 AND status != 'terminated'"
            )
            ad_disabled_users_with_access = one(
                """
                SELECT COUNT(DISTINCT e.id)
                FROM employees e
                JOIN access_records ar ON ar.employee_id = e.id
                WHERE e.ad_enabled = 0
                  AND e.status != 'terminated'
                  AND ar.status IN ('active', 'approved', 'unknown', 'removal_pending')
                """
            )
            last_ad_sync = row_to_dict(
                conn.execute(
                    """
                    SELECT id, source_name, total_rows, created_users, updated_users, disabled_users, created_at
                    FROM ad_sync_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            )
            systems = one("SELECT COUNT(*) FROM systems")
            recent_audit = rows_to_dicts(
                conn.execute(
                    """
                    SELECT id, actor, role, action, entity_type, entity_id, summary, created_at
                    FROM audit_log
                    ORDER BY id DESC
                    LIMIT 8
                    """
                ).fetchall()
            )
            return {
                "activeAccess": active_access,
                "privilegedAccess": privileged_access,
                "staleReviews": stale_reviews,
                "removalsPending": removals_pending,
                "unmatchedImports": unmatched_imports,
                "pendingRequests": pending_requests,
                "expiringAccess": expiring_access,
                "overdueReviews": overdue_reviews,
                "pendingNotifications": pending_notifications,
                "connectorCount": connector_count,
                "riskFindings": risk_count,
                "adDisabledUsers": ad_disabled_users,
                "adDisabledUsersWithAccess": ad_disabled_users_with_access,
                "lastAdSync": last_ad_sync,
                "employees": employees,
                "systems": systems,
                "recentAudit": recent_audit,
            }

    def list_employees(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT e.*,
                           COUNT(ar.id) AS access_count,
                           SUM(CASE WHEN ar.status = 'removal_pending' THEN 1 ELSE 0 END) AS removals_pending
                    FROM employees e
                    LEFT JOIN access_records ar ON ar.employee_id = e.id
                    GROUP BY e.id
                    ORDER BY
                      CASE WHEN e.ad_enabled = 0 AND e.status != 'terminated' THEN 0 ELSE 1 END,
                      e.status ASC,
                      e.name ASC
                    """
                ).fetchall()
            )

    def employee_detail(self, employee_id: int) -> dict:
        with self.session() as conn:
            employee = row_to_dict(
                conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone()
            )
            if not employee:
                raise ApiError(404, "Employee not found")
            access = rows_to_dicts(
                conn.execute(
                    """
                    SELECT ar.*,
                           s.name AS system_name,
                           s.category AS system_category,
                           s.risk_level,
                           s.review_frequency_days,
                           CASE
                             WHEN ar.status IN ('active', 'approved', 'unknown')
                              AND (
                                ar.last_reviewed_at IS NULL
                                OR date(ar.last_reviewed_at) <= date('now', '-' || s.review_frequency_days || ' days')
                              )
                             THEN 1 ELSE 0
                           END AS is_stale
                    FROM access_records ar
                    JOIN systems s ON s.id = ar.system_id
                    WHERE ar.employee_id = ?
                    ORDER BY
                      CASE ar.status
                        WHEN 'removal_pending' THEN 0
                        WHEN 'active' THEN 1
                        WHEN 'unknown' THEN 2
                        WHEN 'approved' THEN 3
                        WHEN 'requested' THEN 4
                        ELSE 5
                      END,
                      s.name
                    """,
                    [employee_id],
                ).fetchall()
            )
            return {"employee": employee, "access": access}

    def list_systems(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT s.*,
                           COUNT(ar.id) AS access_count,
                           SUM(CASE WHEN ar.status = 'removal_pending' THEN 1 ELSE 0 END) AS removals_pending
                    FROM systems s
                    LEFT JOIN access_records ar ON ar.system_id = s.id
                    GROUP BY s.id
                    ORDER BY s.risk_level DESC, s.name ASC
                    """
                ).fetchall()
            )

    def list_access_records(self, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        where = ["1 = 1"]
        values: list[str | int] = []
        if filters.get("status"):
            where.append("ar.status = ?")
            values.append(filters["status"])
        if filters.get("system_id"):
            where.append("ar.system_id = ?")
            values.append(int(filters["system_id"]))
        if filters.get("employee_id"):
            where.append("ar.employee_id = ?")
            values.append(int(filters["employee_id"]))
        if filters.get("q"):
            q = f"%{filters['q'].lower()}%"
            where.append(
                """
                (
                    lower(e.name) LIKE ?
                    OR lower(e.email) LIKE ?
                    OR lower(e.employee_id) LIKE ?
                    OR lower(s.name) LIKE ?
                    OR lower(ar.access_level) LIKE ?
                    OR lower(ar.owner) LIKE ?
                )
                """
            )
            values.extend([q, q, q, q, q, q])
        sql = f"""
            SELECT ar.*,
                   e.name AS employee_name,
                   e.email AS employee_email,
                   e.employee_id AS employee_identifier,
                   e.department,
                   e.location,
                   e.status AS employee_status,
                   e.ad_enabled AS employee_ad_enabled,
                   e.source AS employee_source,
                   e.admin_override AS employee_admin_override,
                   s.name AS system_name,
                   s.category AS system_category,
                   s.risk_level,
                   s.review_frequency_days,
                   CASE
                     WHEN ar.status IN ('active', 'approved', 'unknown')
                      AND (
                        ar.last_reviewed_at IS NULL
                        OR date(ar.last_reviewed_at) <= date('now', '-' || s.review_frequency_days || ' days')
                      )
                     THEN 1 ELSE 0
                   END AS is_stale
            FROM access_records ar
            JOIN employees e ON e.id = ar.employee_id
            JOIN systems s ON s.id = ar.system_id
            WHERE {' AND '.join(where)}
            ORDER BY
              CASE
                WHEN e.status = 'terminated' AND ar.status != 'removed' THEN 0
                WHEN ar.status = 'removal_pending' THEN 1
                WHEN ar.access_type = 'admin' THEN 2
                WHEN is_stale = 1 THEN 3
                ELSE 4
              END,
              e.name,
              s.name
        """
        with self.session() as conn:
            return rows_to_dicts(conn.execute(sql, values).fetchall())

    def create_employee(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["employee_id", "name", "email", "department", "location"])
        now = utc_now()
        data = {
            "employee_id": payload["employee_id"].strip(),
            "name": payload["name"].strip(),
            "email": payload["email"].strip().lower(),
            "department": payload["department"].strip(),
            "location": payload["location"].strip(),
            "manager": payload.get("manager", "").strip() or None,
            "status": payload.get("status", "active"),
            "start_date": payload.get("start_date") or today(),
            "termination_date": payload.get("termination_date") or None,
            "source": payload.get("source", "manual"),
            "ad_object_guid": payload.get("ad_object_guid") or None,
            "ad_sam_account_name": payload.get("ad_sam_account_name") or None,
            "ad_user_principal_name": payload.get("ad_user_principal_name") or None,
            "ad_enabled": payload.get("ad_enabled"),
            "ad_distinguished_name": payload.get("ad_distinguished_name") or None,
            "ad_last_logon_at": payload.get("ad_last_logon_at") or None,
            "ad_last_sync_at": payload.get("ad_last_sync_at") or None,
            "ad_disabled_flagged_at": payload.get("ad_disabled_flagged_at") or None,
            "admin_override": 1 if parse_bool(payload.get("admin_override")) else 0,
            "admin_notes": payload.get("admin_notes", "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        if data["ad_enabled"] is not None:
            parsed_enabled = parse_bool(data["ad_enabled"])
            data["ad_enabled"] = 1 if parsed_enabled else 0 if parsed_enabled is False else None
        if data["status"] not in {"active", "terminated"}:
            raise ApiError(400, "Employee status must be active or terminated")
        with self.session() as conn:
            try:
                employee_pk = insert_row(conn, "employees", data)
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Employee ID or email already exists") from exc
            self._audit(
                conn,
                actor,
                role,
                "create",
                "employee",
                employee_pk,
                f"Created employee {data['name']}.",
                before=None,
                after=data,
            )
        return self.employee_detail(employee_pk)["employee"]

    def update_employee(self, employee_id: int, payload: dict, actor: str, role: str) -> dict:
        allowed = {
            "name",
            "email",
            "department",
            "location",
            "manager",
            "status",
            "start_date",
            "termination_date",
            "admin_override",
            "admin_notes",
        }
        data = {key: payload[key] for key in allowed if key in payload}
        if "email" in data:
            data["email"] = str(data["email"]).lower()
        if "admin_override" in data:
            data["admin_override"] = 1 if parse_bool(data["admin_override"]) else 0
        if "admin_notes" in data:
            data["admin_notes"] = str(data["admin_notes"]).strip() or None
        if "status" in data and data["status"] not in {"active", "terminated"}:
            raise ApiError(400, "Employee status must be active or terminated")
        if data.get("status") == "terminated" and not data.get("termination_date"):
            data["termination_date"] = today()
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(
                conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Employee not found")
            update_row(conn, "employees", employee_id, data)
            if before["status"] != "terminated" and data.get("status") == "terminated":
                due = (date.today() + timedelta(days=3)).isoformat()
                conn.execute(
                    """
                    UPDATE access_records
                    SET status = 'removal_pending',
                        removal_due_at = COALESCE(removal_due_at, ?),
                        updated_at = ?
                    WHERE employee_id = ?
                      AND status IN ('active', 'approved', 'unknown', 'requested')
                    """,
                    [due, utc_now(), employee_id],
                )
            after = row_to_dict(
                conn.execute("SELECT * FROM employees WHERE id = ?", [employee_id]).fetchone()
            )
            self._audit(
                conn,
                actor,
                role,
                "update",
                "employee",
                employee_id,
                f"Updated employee {after['name']}.",
                before=before,
                after=after,
            )
        return self.employee_detail(employee_id)["employee"]

    def create_system(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["name", "category", "owner", "risk_level"])
        now = utc_now()
        name = payload["name"].strip()
        data = {
            "name": name,
            "product_name": payload.get("product_name", "").strip() or name,
            "application_url": normalize_url(payload.get("application_url"), "Application URL"),
            "admin_url": normalize_url(payload.get("admin_url"), "Admin URL"),
            "documentation_url": normalize_url(payload.get("documentation_url"), "Documentation URL"),
            "category": payload["category"],
            "owner": payload["owner"].strip(),
            "risk_level": payload["risk_level"],
            "review_frequency_days": int(payload.get("review_frequency_days") or 90),
            "description": payload.get("description", "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        if data["category"] not in {"software", "physical_location", "network", "shared_resource"}:
            raise ApiError(400, "Unsupported system category")
        if data["risk_level"] not in {"standard", "privileged", "critical"}:
            raise ApiError(400, "Unsupported risk level")
        with self.session() as conn:
            try:
                system_id = insert_row(conn, "systems", data)
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "System or location already exists") from exc
            self._audit(
                conn,
                actor,
                role,
                "create",
                "system",
                system_id,
                f"Created system/location {data['name']}.",
                before=None,
                after=data,
            )
        with self.session() as conn:
            return row_to_dict(conn.execute("SELECT * FROM systems WHERE id = ?", [system_id]).fetchone())

    def create_access_record(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["employee_id", "system_id", "access_level", "access_type", "status", "business_reason", "owner"])
        now = utc_now()
        data = {
            "employee_id": int(payload["employee_id"]),
            "system_id": int(payload["system_id"]),
            "access_level": payload["access_level"].strip(),
            "access_type": payload["access_type"],
            "status": payload["status"],
            "business_reason": payload["business_reason"].strip(),
            "approved_by": payload.get("approved_by", "").strip() or None,
            "approved_at": payload.get("approved_at") or None,
            "owner": payload["owner"].strip(),
            "last_reviewed_at": payload.get("last_reviewed_at") or None,
            "removal_due_at": payload.get("removal_due_at") or None,
            "removed_at": payload.get("removed_at") or None,
            "removal_evidence": payload.get("removal_evidence", "").strip() or None,
            "expires_at": payload.get("expires_at") or payload.get("expiration_date") or None,
            "evidence_url": payload.get("evidence_url", "").strip() or None,
            "evidence_notes": payload.get("evidence_notes", "").strip() or None,
            "mfa_enabled": self._optional_bool_int(payload.get("mfa_enabled")),
            "last_rotated_at": payload.get("last_rotated_at") or None,
            "rotation_due_at": payload.get("rotation_due_at") or None,
            "notes": payload.get("notes", "").strip() or None,
            "source_import_run_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self._require_removed_access_evidence(data, payload)
        self._validate_access_values(data)
        with self.session() as conn:
            self._require_employee_and_system(conn, data["employee_id"], data["system_id"])
            record_id = insert_row(conn, "access_records", data)
            self._audit(
                conn,
                actor,
                role,
                "create",
                "access_record",
                record_id,
                "Created access record.",
                before=None,
                after=data,
            )
        return self.get_access_record(record_id)

    def get_access_record(self, record_id: int) -> dict:
        records = self.list_access_records({})
        for record in records:
            if record["id"] == record_id:
                return record
        raise ApiError(404, "Access record not found")

    def update_access_record(self, record_id: int, payload: dict, actor: str, role: str) -> dict:
        allowed = {
            "access_level",
            "access_type",
            "status",
            "business_reason",
            "approved_by",
            "approved_at",
            "owner",
            "last_reviewed_at",
            "removal_due_at",
            "removed_at",
            "removal_evidence",
            "expires_at",
            "evidence_url",
            "evidence_notes",
            "mfa_enabled",
            "last_rotated_at",
            "rotation_due_at",
            "notes",
        }
        data = {key: payload[key] for key in allowed if key in payload}
        if "mfa_enabled" in data:
            data["mfa_enabled"] = self._optional_bool_int(data["mfa_enabled"])
        self._require_removed_access_evidence(data, payload)
        if data.get("status") == "removal_pending" and not data.get("removal_due_at"):
            data["removal_due_at"] = (date.today() + timedelta(days=3)).isoformat()
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(
                conn.execute("SELECT * FROM access_records WHERE id = ?", [record_id]).fetchone()
            )
            if not before:
                raise ApiError(404, "Access record not found")
            candidate = dict(before)
            candidate.update(data)
            self._validate_access_values(candidate)
            update_row(conn, "access_records", record_id, data)
            after = row_to_dict(
                conn.execute("SELECT * FROM access_records WHERE id = ?", [record_id]).fetchone()
            )
            self._audit(
                conn,
                actor,
                role,
                "update",
                "access_record",
                record_id,
                "Updated access record.",
                before=before,
                after=after,
            )
        return self.get_access_record(record_id)

    def review_access_record(self, record_id: int, payload: dict, actor: str, role: str) -> dict:
        decision = payload.get("decision")
        if decision not in {"certified", "remove"}:
            raise ApiError(400, "Decision must be certified or remove")
        if decision == "certified":
            update = {
                "status": "active",
                "last_reviewed_at": today(),
                "notes": payload.get("notes", "").strip() or "Access certified during review.",
            }
        else:
            update = {
                "status": "removal_pending",
                "removal_due_at": (date.today() + timedelta(days=3)).isoformat(),
                "notes": payload.get("notes", "").strip() or "Reviewer requested access removal.",
            }
        return self.update_access_record(record_id, update, actor, role)

    def _validate_access_values(self, data: dict) -> None:
        if data["access_type"] not in {
            "user",
            "admin",
            "building_code",
            "badge",
            "shared_account",
            "vendor",
            "service_account",
        }:
            raise ApiError(400, "Unsupported access type")
        if data["status"] not in {"requested", "approved", "active", "removal_pending", "removed", "unknown"}:
            raise ApiError(400, "Unsupported access status")

    def _require_removed_access_evidence(self, data: dict, payload: dict) -> None:
        if data.get("status") != "removed":
            return
        data["removed_at"] = data.get("removed_at") or today()
        evidence = str(data.get("removal_evidence") or payload.get("removal_evidence") or "").strip()
        if not evidence:
            raise ApiError(400, "Removal evidence is required when access is marked removed")
        data["removal_evidence"] = evidence

    def _optional_bool_int(self, value) -> int | None:
        parsed = parse_bool(value)
        if parsed is None:
            return None
        return 1 if parsed else 0

    def _require_employee_and_system(
        self, conn: sqlite3.Connection, employee_id: int, system_id: int
    ) -> None:
        if not conn.execute("SELECT id FROM employees WHERE id = ?", [employee_id]).fetchone():
            raise ApiError(400, "Employee does not exist")
        if not conn.execute("SELECT id FROM systems WHERE id = ?", [system_id]).fetchone():
            raise ApiError(400, "System or location does not exist")

    def list_imports(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT ir.*, s.name AS system_name
                    FROM import_runs ir
                    JOIN systems s ON s.id = ir.system_id
                    ORDER BY ir.id DESC
                    """
                ).fetchall()
            )

    def import_accounts(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["system_id", "csv_text"])
        system_id = int(payload["system_id"])
        source_name = payload.get("source_name", "CSV import").strip() or "CSV import"
        csv_text = payload["csv_text"].strip()
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise ApiError(400, "CSV text must include a header row")
        now = utc_now()
        rows = list(reader)
        if not rows:
            raise ApiError(400, "CSV text must include at least one account row")

        with self.session() as conn:
            system = row_to_dict(conn.execute("SELECT * FROM systems WHERE id = ?", [system_id]).fetchone())
            if not system:
                raise ApiError(400, "System or location does not exist")
            run_id = insert_row(
                conn,
                "import_runs",
                {
                    "system_id": system_id,
                    "source_name": source_name,
                    "imported_by": actor,
                    "total_rows": 0,
                    "matched_rows": 0,
                    "unmatched_rows": 0,
                    "inactive_employee_rows": 0,
                    "created_access_records": 0,
                    "created_at": now,
                },
            )
            stats = {
                "total_rows": 0,
                "matched_rows": 0,
                "unmatched_rows": 0,
                "inactive_employee_rows": 0,
                "created_access_records": 0,
            }
            accounts: list[dict] = []
            for source_row in rows:
                normalized = {str(key).strip().lower(): (value or "").strip() for key, value in source_row.items()}
                email = self._pick(normalized, ["email", "user_email", "mail"]).lower()
                employee_identifier = self._pick(normalized, ["employee_id", "employee", "id", "employee_number"])
                display_name = self._pick(normalized, ["name", "full_name", "display_name", "display"])
                system_account = (
                    self._pick(normalized, ["account", "username", "user", "login"])
                    or email
                    or display_name
                    or employee_identifier
                    or "unknown-account"
                )
                access_level = self._pick(normalized, ["access_level", "role", "permission", "group"]) or "User"
                access_type = (
                    self._pick(normalized, ["access_type", "type"])
                    or ("admin" if "admin" in access_level.lower() else "user")
                )
                if access_type not in {
                    "user",
                    "admin",
                    "building_code",
                    "badge",
                    "shared_account",
                    "vendor",
                    "service_account",
                }:
                    access_type = "user"
                employee = self._match_employee(conn, employee_identifier, email, display_name)
                status = "unmatched"
                matched_employee_id = None
                if employee:
                    matched_employee_id = employee["id"]
                    status = "inactive_employee" if employee["status"] == "terminated" else "matched"
                stats["total_rows"] += 1
                if status == "matched":
                    stats["matched_rows"] += 1
                elif status == "inactive_employee":
                    stats["inactive_employee_rows"] += 1
                else:
                    stats["unmatched_rows"] += 1

                account_id = insert_row(
                    conn,
                    "import_accounts",
                    {
                        "import_run_id": run_id,
                        "system_account": system_account,
                        "display_name": display_name or None,
                        "email": email or None,
                        "employee_identifier": employee_identifier or None,
                        "access_level": access_level,
                        "access_type": access_type,
                        "matched_employee_id": matched_employee_id,
                        "status": status,
                        "raw_json": json.dumps(source_row, sort_keys=True),
                        "created_at": now,
                    },
                )
                account = row_to_dict(
                    conn.execute("SELECT * FROM import_accounts WHERE id = ?", [account_id]).fetchone()
                )
                accounts.append(account)

                if employee:
                    created = self._create_access_from_import(
                        conn,
                        employee,
                        system,
                        run_id,
                        access_level,
                        access_type,
                        actor,
                    )
                    if created:
                        stats["created_access_records"] += 1

            update_row(conn, "import_runs", run_id, stats)
            self._audit(
                conn,
                actor,
                role,
                "import",
                "import_run",
                run_id,
                f"Imported {stats['total_rows']} account row(s) for {system['name']}.",
                before=None,
                after=stats,
            )
            run = row_to_dict(conn.execute("SELECT * FROM import_runs WHERE id = ?", [run_id]).fetchone())
            run["accounts"] = accounts
            return run

    def list_ad_sync_runs(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT *
                    FROM ad_sync_runs
                    ORDER BY id DESC
                    LIMIT 25
                    """
                ).fetchall()
            )

    def sync_ad_users(self, payload: dict, actor: str, role: str) -> dict:
        source_name = payload.get("source_name", "Active Directory sync").strip() or "Active Directory sync"
        raw_format = payload.get("format", "csv")
        source_format = str(raw_format).strip().lower()
        directory_text = (
            payload.get("directory_text")
            or payload.get("csv_text")
            or payload.get("json_text")
            or ""
        ).strip()
        if source_format not in {"csv", "json"}:
            raise ApiError(400, "AD sync format must be csv or json")
        if not directory_text:
            raise ApiError(400, "AD sync payload is required")

        rows = self._parse_ad_rows(directory_text, source_format)
        if not rows:
            raise ApiError(400, "AD sync payload must include at least one user row")

        now = utc_now()
        stats = {
            "total_rows": 0,
            "created_users": 0,
            "updated_users": 0,
            "disabled_users": 0,
            "reenabled_users": 0,
            "preserved_overrides": 0,
            "error_rows": 0,
        }
        errors: list[dict] = []
        changed_ids: list[int] = []

        with self.session() as conn:
            run_id = insert_row(
                conn,
                "ad_sync_runs",
                {
                    "source_name": source_name,
                    "imported_by": actor,
                    "format": source_format,
                    **stats,
                    "created_at": now,
                },
            )
            for index, source_row in enumerate(rows, start=1):
                stats["total_rows"] += 1
                try:
                    ad_user = self._normalize_ad_user(source_row, now)
                    if not ad_user["employee_id"]:
                        raise ApiError(400, "AD row has no employee ID, SAM account name, or UPN")
                    employee = self._match_ad_employee(conn, ad_user)
                    if employee:
                        employee_id = self._update_employee_from_ad(conn, employee, ad_user, stats)
                        stats["updated_users"] += 1
                    else:
                        employee_id = self._create_employee_from_ad(conn, ad_user)
                        stats["created_users"] += 1
                    changed_ids.append(employee_id)
                    if ad_user["ad_enabled"] == 0:
                        stats["disabled_users"] += 1
                except Exception as exc:
                    stats["error_rows"] += 1
                    errors.append({"row": index, "error": str(exc)})

            update_row(conn, "ad_sync_runs", run_id, stats)
            self._audit(
                conn,
                actor,
                role,
                "ad_sync",
                "ad_sync_run",
                run_id,
                f"Synced {stats['total_rows']} Active Directory user row(s).",
                before=None,
                after={**stats, "changed_employee_ids": changed_ids[:50], "errors": errors[:10]},
            )
            run = row_to_dict(conn.execute("SELECT * FROM ad_sync_runs WHERE id = ?", [run_id]).fetchone())
            run["errors"] = errors
            return run

    def get_ad_sync_settings(self, include_payload: bool = True) -> dict:
        with self.session() as conn:
            settings = row_to_dict(conn.execute("SELECT * FROM ad_sync_settings WHERE id = 1").fetchone())
        if not settings:
            return {}
        has_directory_payload = bool(settings.get("directory_text"))
        if not include_payload:
            settings["directory_text"] = None
        settings["has_directory_payload"] = has_directory_payload
        return settings

    def update_ad_sync_settings(self, payload: dict, actor: str, role: str) -> dict:
        data = {}
        for key in ["source_name", "format", "directory_text", "interval_hours", "next_run_at"]:
            if key in payload:
                data[key] = payload[key]
        if "enabled" in payload:
            data["enabled"] = 1 if parse_bool(payload["enabled"]) else 0
        if "format" in data and str(data["format"]).lower() not in {"csv", "json"}:
            raise ApiError(400, "Scheduled AD sync format must be csv or json")
        if "interval_hours" in data:
            data["interval_hours"] = max(1, int(data["interval_hours"]))
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM ad_sync_settings WHERE id = 1").fetchone())
            update_row(conn, "ad_sync_settings", 1, data)
            after = row_to_dict(conn.execute("SELECT * FROM ad_sync_settings WHERE id = 1").fetchone())
            self._audit(conn, actor, role, "update", "ad_sync_settings", 1, "Updated scheduled AD sync settings.", before, after)
            return after

    def run_scheduled_ad_sync(self, actor: str = "Scheduler", role: str = "Admin", force: bool = False) -> dict:
        settings = self.get_ad_sync_settings()
        if not settings or not settings["enabled"]:
            return {"skipped": True, "reason": "Scheduled AD sync is disabled"}
        if not settings.get("directory_text"):
            return {"skipped": True, "reason": "No scheduled AD payload is configured"}
        now_dt = datetime.now(timezone.utc)
        next_run = parse_utc(settings.get("next_run_at"))
        if next_run and next_run > now_dt and not force:
            return {"skipped": True, "reason": "Next AD sync is not due yet", "next_run_at": settings["next_run_at"]}
        try:
            run = self.sync_ad_users(
                {
                    "source_name": settings["source_name"],
                    "format": settings["format"],
                    "directory_text": settings["directory_text"],
                },
                actor=actor,
                role=role,
            )
        except Exception as error:
            self.record_scheduled_ad_sync_failure(error, actor=actor, role=role)
            raise
        interval = int(settings.get("interval_hours") or 24)
        next_value = (now_dt + timedelta(hours=interval)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.session() as conn:
            update_row(
                conn,
                "ad_sync_settings",
                1,
                {
                    "last_run_at": utc_now(),
                    "next_run_at": next_value,
                    "last_status": "complete",
                    "updated_at": utc_now(),
                },
            )
        return {"skipped": False, "adSyncRun": run, "next_run_at": next_value}

    def record_scheduled_ad_sync_failure(self, error: Exception, actor: str = "Scheduler", role: str = "Admin") -> dict:
        now = utc_now()
        message = str(error) or error.__class__.__name__
        update = {"last_run_at": now, "last_status": f"failed: {message}", "updated_at": now}
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM ad_sync_settings WHERE id = 1").fetchone())
            update_row(conn, "ad_sync_settings", 1, update)
            after = row_to_dict(conn.execute("SELECT * FROM ad_sync_settings WHERE id = 1").fetchone())
            notification_id = insert_row(
                conn,
                "notifications",
                {
                    "severity": "high",
                    "recipient": "IT Security",
                    "subject": "Scheduled AD sync failed",
                    "body": f"Review the saved scheduled AD sync payload. Error: {message}",
                    "status": "pending",
                    "source_type": "scheduled_ad_sync",
                    "source_id": 1,
                    "due_date": today(),
                    "created_at": now,
                    "acknowledged_at": None,
                },
            )
            after["notification_id"] = notification_id
            self._audit(
                conn,
                actor,
                role,
                "scheduled_ad_sync_failed",
                "ad_sync_settings",
                1,
                f"Scheduled AD sync failed: {message}",
                before=before,
                after=after,
            )
            return after

    def disabled_access_queue(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT ar.*,
                           e.name AS employee_name,
                           e.employee_id AS employee_identifier,
                           e.email AS employee_email,
                           e.ad_disabled_flagged_at,
                           s.name AS system_name,
                           s.risk_level,
                           s.owner AS system_owner
                    FROM access_records ar
                    JOIN employees e ON e.id = ar.employee_id
                    JOIN systems s ON s.id = ar.system_id
                    WHERE e.ad_enabled = 0
                      AND e.status != 'terminated'
                      AND ar.status IN ('active', 'approved', 'unknown', 'removal_pending')
                    ORDER BY s.risk_level DESC, e.name, s.name
                    """
                ).fetchall()
            )

    def route_disabled_access_to_removal(self, actor: str, role: str) -> dict:
        due = (date.today() + timedelta(days=3)).isoformat()
        now = utc_now()
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT ar.id, e.name AS employee_name, s.name AS system_name, ar.status
                FROM access_records ar
                JOIN employees e ON e.id = ar.employee_id
                JOIN systems s ON s.id = ar.system_id
                WHERE e.ad_enabled = 0
                  AND e.status != 'terminated'
                  AND ar.status IN ('active', 'approved', 'unknown', 'requested')
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join(["?"] * len(ids))
                conn.execute(
                    f"""
                    UPDATE access_records
                    SET status = 'removal_pending',
                        removal_due_at = COALESCE(removal_due_at, ?),
                        notes = COALESCE(notes, 'Disabled AD user routed to access removal.'),
                        updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    [due, now, *ids],
                )
            notification_id = None
            if ids:
                notification_id = insert_row(
                    conn,
                    "notifications",
                    {
                        "severity": "critical",
                        "recipient": "IT Security",
                        "subject": f"{len(ids)} disabled-user access record(s) routed to removal",
                        "body": "Review the disabled-user queue and collect removal evidence for each affected system.",
                        "status": "pending",
                        "source_type": "disabled_access_queue",
                        "source_id": None,
                        "due_date": due,
                        "created_at": now,
                        "acknowledged_at": None,
                    },
                )
            result = {"routed": len(ids), "due_date": due, "notification_id": notification_id}
            self._audit(
                conn,
                actor,
                role,
                "route_removal",
                "disabled_access_queue",
                None,
                f"Routed {len(ids)} disabled-user access record(s) to removal.",
                before=None,
                after=result,
            )
            return result

    def list_access_requests(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT req.*,
                           e.name AS employee_name,
                           e.email AS employee_email,
                           s.name AS system_name,
                           s.owner AS system_owner
                    FROM access_requests req
                    JOIN employees e ON e.id = req.employee_id
                    JOIN systems s ON s.id = req.system_id
                    ORDER BY
                      CASE req.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                      req.created_at DESC
                    """
                ).fetchall()
            )

    def create_access_request(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["requester", "employee_id", "system_id", "access_level", "access_type", "business_reason"])
        now = utc_now()
        data = {
            "requester": payload["requester"].strip(),
            "employee_id": int(payload["employee_id"]),
            "system_id": int(payload["system_id"]),
            "access_level": payload["access_level"].strip(),
            "access_type": payload["access_type"],
            "business_reason": payload["business_reason"].strip(),
            "expiration_date": payload.get("expiration_date") or None,
            "status": "pending",
            "approver": None,
            "decided_at": None,
            "decision_notes": None,
            "created_access_record_id": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.session() as conn:
            self._require_employee_and_system(conn, data["employee_id"], data["system_id"])
            request_id = insert_row(conn, "access_requests", data)
            self._audit(conn, actor, role, "create", "access_request", request_id, "Created access request.", None, data)
        return next(request for request in self.list_access_requests() if request["id"] == request_id)

    def decide_access_request(self, request_id: int, payload: dict, actor: str, role: str) -> dict:
        decision = payload.get("decision")
        if decision not in {"approve", "deny"}:
            raise ApiError(400, "Decision must be approve or deny")
        now = utc_now()
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM access_requests WHERE id = ?", [request_id]).fetchone())
            if not before:
                raise ApiError(404, "Access request not found")
            if before["status"] != "pending":
                raise ApiError(409, "Only pending requests can be decided")
            created_access_record_id = None
            status = "denied"
            if decision == "approve":
                system = row_to_dict(conn.execute("SELECT * FROM systems WHERE id = ?", [before["system_id"]]).fetchone())
                created_access_record_id = insert_row(
                    conn,
                    "access_records",
                    {
                        "employee_id": before["employee_id"],
                        "system_id": before["system_id"],
                        "access_level": before["access_level"],
                        "access_type": before["access_type"],
                        "status": "active",
                        "business_reason": before["business_reason"],
                        "approved_by": payload.get("approver") or actor,
                        "approved_at": today(),
                        "owner": system["owner"],
                        "last_reviewed_at": None,
                        "removal_due_at": None,
                        "removed_at": None,
                        "removal_evidence": None,
                        "expires_at": before["expiration_date"],
                        "evidence_url": payload.get("evidence_url") or None,
                        "evidence_notes": payload.get("decision_notes") or "Approved access request.",
                        "mfa_enabled": None,
                        "last_rotated_at": None,
                        "rotation_due_at": None,
                        "notes": f"Created from access request {request_id}.",
                        "source_import_run_id": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                status = "fulfilled"
            update = {
                "status": status,
                "approver": payload.get("approver") or actor,
                "decided_at": now,
                "decision_notes": payload.get("decision_notes") or None,
                "created_access_record_id": created_access_record_id,
                "updated_at": now,
            }
            update_row(conn, "access_requests", request_id, update)
            after = row_to_dict(conn.execute("SELECT * FROM access_requests WHERE id = ?", [request_id]).fetchone())
            self._audit(conn, actor, role, "decide", "access_request", request_id, f"{decision.title()}d access request.", before, after)
        return next(request for request in self.list_access_requests() if request["id"] == request_id)

    def list_review_campaigns(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT rc.*, s.name AS system_name
                    FROM review_campaigns rc
                    LEFT JOIN systems s ON s.id = rc.system_id
                    ORDER BY
                      CASE rc.status WHEN 'open' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,
                      date(rc.due_date) ASC
                    """
                ).fetchall()
            )

    def create_review_campaign(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["name", "owner", "due_date"])
        now = utc_now()
        data = {
            "name": payload["name"].strip(),
            "owner": payload["owner"].strip(),
            "system_id": int(payload["system_id"]) if payload.get("system_id") else None,
            "frequency_days": int(payload.get("frequency_days") or 90),
            "due_date": payload["due_date"],
            "status": payload.get("status", "open"),
            "completed_at": None,
            "notes": payload.get("notes", "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        if data["status"] not in {"planned", "open", "complete"}:
            raise ApiError(400, "Review campaign status is invalid")
        with self.session() as conn:
            campaign_id = insert_row(conn, "review_campaigns", data)
            self._audit(conn, actor, role, "create", "review_campaign", campaign_id, f"Created review campaign {data['name']}.", None, data)
        return next(campaign for campaign in self.list_review_campaigns() if campaign["id"] == campaign_id)

    def update_review_campaign(self, campaign_id: int, payload: dict, actor: str, role: str) -> dict:
        allowed = {"name", "owner", "system_id", "frequency_days", "due_date", "status", "notes"}
        data = {key: payload[key] for key in allowed if key in payload}
        if "status" in data and data["status"] not in {"planned", "open", "complete"}:
            raise ApiError(400, "Review campaign status is invalid")
        if data.get("status") == "complete":
            data["completed_at"] = utc_now()
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM review_campaigns WHERE id = ?", [campaign_id]).fetchone())
            if not before:
                raise ApiError(404, "Review campaign not found")
            update_row(conn, "review_campaigns", campaign_id, data)
            after = row_to_dict(conn.execute("SELECT * FROM review_campaigns WHERE id = ?", [campaign_id]).fetchone())
            self._audit(conn, actor, role, "update", "review_campaign", campaign_id, "Updated review campaign.", before, after)
        return next(campaign for campaign in self.list_review_campaigns() if campaign["id"] == campaign_id)

    def list_notifications(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT *
                    FROM notifications
                    ORDER BY
                      CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                      CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                      created_at DESC
                    LIMIT 200
                    """
                ).fetchall()
            )

    def acknowledge_notification(self, notification_id: int, actor: str, role: str) -> dict:
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM notifications WHERE id = ?", [notification_id]).fetchone())
            if not before:
                raise ApiError(404, "Notification not found")
            update = {"status": "acknowledged", "acknowledged_at": utc_now()}
            update_row(conn, "notifications", notification_id, update)
            after = row_to_dict(conn.execute("SELECT * FROM notifications WHERE id = ?", [notification_id]).fetchone())
            self._audit(conn, actor, role, "acknowledge", "notification", notification_id, "Acknowledged notification.", before, after)
            return after

    def risk_findings(self) -> list[dict]:
        findings: list[dict] = []
        severity_rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT ar.id AS access_record_id, e.name AS employee_name, e.status AS employee_status,
                       e.ad_enabled, s.name AS system_name, s.risk_level, ar.status, ar.access_type,
                       ar.last_reviewed_at, ar.expires_at, ar.removal_due_at
                FROM access_records ar
                JOIN employees e ON e.id = ar.employee_id
                JOIN systems s ON s.id = ar.system_id
                WHERE ar.status IN ('active', 'approved', 'unknown', 'removal_pending')
                """
            ).fetchall()
            for row in rows:
                if row["employee_status"] == "terminated" and row["status"] != "removed":
                    findings.append(self._risk("critical", "Terminated employee still has access", row["employee_name"], row["system_name"], "Route access to removal and collect evidence.", row["access_record_id"]))
                if row["ad_enabled"] == 0 and row["status"] != "removed":
                    findings.append(self._risk("critical", "AD-disabled user still has access", row["employee_name"], row["system_name"], "Use the disabled-user queue to remove this access.", row["access_record_id"]))
                expires = parse_date(row["expires_at"])
                if expires and expires <= date.today() and row["status"] != "removed":
                    findings.append(self._risk("high", "Access is expired", row["employee_name"], row["system_name"], "Review or remove expired temporary access.", row["access_record_id"]))
                elif expires and expires <= date.today() + timedelta(days=14):
                    findings.append(self._risk("medium", "Access expires soon", row["employee_name"], row["system_name"], "Confirm extension or schedule removal.", row["access_record_id"]))
                removal_due = parse_date(row["removal_due_at"])
                if row["status"] == "removal_pending" and removal_due and removal_due < date.today():
                    findings.append(self._risk("high", "Removal is overdue", row["employee_name"], row["system_name"], "Escalate overdue removal evidence.", row["access_record_id"]))
            for row in conn.execute("SELECT * FROM shared_accounts WHERE status != 'disabled'").fetchall():
                rotation_due = parse_date(row["rotation_due_at"])
                if not row["mfa_enabled"]:
                    findings.append(self._risk("high", "Shared account has no MFA evidence", row["account_name"], "Shared account", "Enable MFA or document compensating controls.", row["id"]))
                if rotation_due and rotation_due <= date.today():
                    findings.append(self._risk("medium", "Shared account rotation is due", row["account_name"], "Shared account", "Rotate credentials and update evidence.", row["id"]))
            for row in conn.execute("SELECT * FROM physical_credentials WHERE status IN ('active', 'return_pending')").fetchall():
                due = parse_date(row["due_at"])
                if row["status"] == "return_pending" and due and due < date.today():
                    findings.append(self._risk("high", "Physical credential return is overdue", row["credential_identifier"] or row["credential_type"], row["location"], "Recover badge/key/fob or record rotation evidence.", row["id"]))
        return sorted(findings, key=lambda item: (severity_rank[item["severity"]], item["title"], item["subject"]))

    def _risk(self, severity: str, title: str, subject: str, target: str, recommendation: str, source_id: int | None) -> dict:
        return {
            "severity": severity,
            "title": title,
            "subject": subject,
            "target": target,
            "recommendation": recommendation,
            "source_id": source_id,
        }

    def list_shared_accounts(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT sa.*, s.name AS system_name
                    FROM shared_accounts sa
                    JOIN systems s ON s.id = sa.system_id
                    ORDER BY sa.status, sa.rotation_due_at, sa.account_name
                    """
                ).fetchall()
            )

    def create_shared_account(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["system_id", "account_name", "owner", "business_reason"])
        now = utc_now()
        data = {
            "system_id": int(payload["system_id"]),
            "account_name": payload["account_name"].strip(),
            "owner": payload["owner"].strip(),
            "approved_users": payload.get("approved_users", "").strip() or None,
            "mfa_enabled": 1 if parse_bool(payload.get("mfa_enabled")) else 0,
            "last_rotated_at": payload.get("last_rotated_at") or None,
            "rotation_due_at": payload.get("rotation_due_at") or None,
            "business_reason": payload["business_reason"].strip(),
            "status": payload.get("status", "active"),
            "notes": payload.get("notes", "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        with self.session() as conn:
            if not conn.execute("SELECT id FROM systems WHERE id = ?", [data["system_id"]]).fetchone():
                raise ApiError(400, "System or location does not exist")
            shared_id = insert_row(conn, "shared_accounts", data)
            self._audit(conn, actor, role, "create", "shared_account", shared_id, f"Created shared account {data['account_name']}.", None, data)
        return next(item for item in self.list_shared_accounts() if item["id"] == shared_id)

    def list_physical_credentials(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT pc.*, e.name AS employee_name, e.employee_id AS employee_identifier, s.name AS system_name
                    FROM physical_credentials pc
                    JOIN employees e ON e.id = pc.employee_id
                    LEFT JOIN systems s ON s.id = pc.system_id
                    ORDER BY
                      CASE pc.status WHEN 'return_pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                      pc.due_at,
                      e.name
                    """
                ).fetchall()
            )

    def create_physical_credential(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["employee_id", "location", "credential_type"])
        now = utc_now()
        data = {
            "employee_id": int(payload["employee_id"]),
            "system_id": int(payload["system_id"]) if payload.get("system_id") else None,
            "location": payload["location"].strip(),
            "credential_type": payload["credential_type"],
            "credential_identifier": payload.get("credential_identifier", "").strip() or None,
            "zone": payload.get("zone", "").strip() or None,
            "status": payload.get("status", "active"),
            "issued_at": payload.get("issued_at") or today(),
            "due_at": payload.get("due_at") or None,
            "completed_at": payload.get("completed_at") or None,
            "evidence": payload.get("evidence", "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        if data["credential_type"] not in {"badge", "key", "code", "fob"}:
            raise ApiError(400, "Unsupported physical credential type")
        if data["status"] not in {"active", "return_pending", "returned", "rotated"}:
            raise ApiError(400, "Unsupported physical credential status")
        with self.session() as conn:
            if not conn.execute("SELECT id FROM employees WHERE id = ?", [data["employee_id"]]).fetchone():
                raise ApiError(400, "Employee does not exist")
            credential_id = insert_row(conn, "physical_credentials", data)
            self._audit(conn, actor, role, "create", "physical_credential", credential_id, "Created physical credential.", None, data)
        return next(item for item in self.list_physical_credentials() if item["id"] == credential_id)

    def list_connectors(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(conn.execute("SELECT * FROM connectors ORDER BY status, name").fetchall())

    def create_connector(self, payload: dict, actor: str, role: str) -> dict:
        require_fields(payload, ["name", "connector_type", "owner"])
        now = utc_now()
        data = {
            "name": payload["name"].strip(),
            "connector_type": payload["connector_type"].strip(),
            "owner": payload["owner"].strip(),
            "status": payload.get("status", "planned"),
            "instructions": payload.get("instructions", "").strip() or None,
            "last_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.session() as conn:
            try:
                connector_id = insert_row(conn, "connectors", data)
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "Connector already exists") from exc
            self._audit(conn, actor, role, "create", "connector", connector_id, f"Created connector plan {data['name']}.", None, data)
        return next(connector for connector in self.list_connectors() if connector["id"] == connector_id)

    def owner_dashboard(self, owner: str | None = None) -> dict:
        with self.session() as conn:
            owner_filter = "WHERE s.owner = ?" if owner else ""
            values = [owner] if owner else []
            systems = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT s.*,
                           COUNT(ar.id) AS access_count,
                           SUM(CASE WHEN ar.status = 'removal_pending' THEN 1 ELSE 0 END) AS removals_pending,
                           SUM(CASE WHEN ar.status IN ('active', 'approved', 'unknown') AND (ar.last_reviewed_at IS NULL OR date(ar.last_reviewed_at) <= date('now', '-' || s.review_frequency_days || ' days')) THEN 1 ELSE 0 END) AS review_due
                    FROM systems s
                    LEFT JOIN access_records ar ON ar.system_id = s.id
                    {owner_filter}
                    GROUP BY s.id
                    ORDER BY removals_pending DESC, review_due DESC, s.name
                    """,
                    values,
                ).fetchall()
            )
            return {"owner": owner or "All owners", "systems": systems}

    def list_backups(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(conn.execute("SELECT * FROM backup_runs ORDER BY id DESC LIMIT 25").fetchall())

    def run_backup(self, payload: dict, actor: str, role: str) -> dict:
        retention_days = int(payload.get("retention_days") or 90)
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"access_register_{stamp}.db"
        status = "complete"
        error = None
        size = None
        try:
            shutil.copy2(self.db_path, backup_path)
            size = backup_path.stat().st_size
        except Exception as exc:
            status = "failed"
            error = str(exc)
        with self.session() as conn:
            backup_id = insert_row(
                conn,
                "backup_runs",
                {
                    "backup_path": str(backup_path),
                    "status": status,
                    "retention_days": retention_days,
                    "size_bytes": size,
                    "error": error,
                    "created_at": utc_now(),
                },
            )
            self._audit(conn, actor, role, "backup", "backup_run", backup_id, f"Backup {status}.", None, {"path": str(backup_path), "status": status})
            return row_to_dict(conn.execute("SELECT * FROM backup_runs WHERE id = ?", [backup_id]).fetchone())

    def get_auth_settings(self) -> dict:
        with self.session() as conn:
            return row_to_dict(conn.execute("SELECT * FROM auth_settings WHERE id = 1").fetchone())

    def update_auth_settings(self, payload: dict, actor: str, role: str) -> dict:
        allowed = {"provider", "login_required", "admin_group", "reviewer_group", "hr_group", "readonly_group", "notes"}
        data = {key: payload[key] for key in allowed if key in payload}
        if "login_required" in data:
            data["login_required"] = 1 if parse_bool(data["login_required"]) else 0
        data["updated_at"] = utc_now()
        with self.session() as conn:
            before = row_to_dict(conn.execute("SELECT * FROM auth_settings WHERE id = 1").fetchone())
            update_row(conn, "auth_settings", 1, data)
            after = row_to_dict(conn.execute("SELECT * FROM auth_settings WHERE id = 1").fetchone())
            self._audit(conn, actor, role, "update", "auth_settings", 1, "Updated authentication settings.", before, after)
            return after

    def audit_log_csv(self) -> str:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT id, actor, role, action, entity_type, entity_id, summary, created_at
                FROM audit_log
                ORDER BY id DESC
                """
            ).fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "actor", "role", "action", "entity_type", "entity_id", "summary", "created_at"])
        for row in rows:
            writer.writerow(
                [
                    csv_safe_cell(row["id"]),
                    csv_safe_cell(row["actor"]),
                    csv_safe_cell(row["role"]),
                    csv_safe_cell(row["action"]),
                    csv_safe_cell(row["entity_type"]),
                    csv_safe_cell(row["entity_id"]),
                    csv_safe_cell(row["summary"]),
                    csv_safe_cell(row["created_at"]),
                ]
            )
        return output.getvalue()

    def _parse_ad_rows(self, directory_text: str, source_format: str) -> list[dict]:
        if source_format == "csv":
            reader = csv.DictReader(io.StringIO(directory_text))
            if not reader.fieldnames:
                raise ApiError(400, "AD CSV must include a header row")
            return list(reader)
        try:
            payload = json.loads(directory_text)
        except json.JSONDecodeError as exc:
            raise ApiError(400, "AD JSON payload is invalid") from exc
        if isinstance(payload, dict):
            if isinstance(payload.get("users"), list):
                payload = payload["users"]
            elif isinstance(payload.get("value"), list):
                payload = payload["value"]
            else:
                payload = [payload]
        if not isinstance(payload, list):
            raise ApiError(400, "AD JSON payload must be a user object or an array of user objects")
        if not all(isinstance(row, dict) for row in payload):
            raise ApiError(400, "AD JSON rows must be objects")
        return payload

    def _normalize_ad_user(self, source_row: dict, now: str) -> dict:
        row = {str(key).strip().lower(): value for key, value in source_row.items()}
        text_row = {key: "" if value is None else str(value).strip() for key, value in row.items()}
        sam = self._pick(text_row, ["samaccountname", "sam_account_name", "sam", "accountname"])
        upn = self._pick(text_row, ["userprincipalname", "user_principal_name", "upn"])
        email = self._pick(text_row, ["mail", "email", "emailaddress", "user_email"]).lower()
        if not email and "@" in upn:
            email = upn.lower()
        employee_id = self._pick(
            text_row,
            ["employeeid", "employee_id", "employeenumber", "employee_number", "id"],
        ) or sam or upn
        name = self._pick(text_row, ["displayname", "display_name", "name", "cn"])
        if not name:
            first = self._pick(text_row, ["givenname", "given_name", "first_name"])
            last = self._pick(text_row, ["surname", "sn", "last_name"])
            name = f"{first} {last}".strip()
        enabled = parse_bool(self._pick(text_row, ["enabled", "ad_enabled", "accountenabled"]))
        disabled_value = parse_bool(self._pick(text_row, ["disabled", "isdisabled", "accountdisabled"]))
        if enabled is None and disabled_value is not None:
            enabled = not disabled_value
        if enabled is None:
            enabled = True
        return {
            "employee_id": employee_id.strip(),
            "name": name.strip() or employee_id.strip(),
            "email": email or (upn.lower() if upn else f"{employee_id.strip().lower()}@unknown.local"),
            "department": self._pick(text_row, ["department", "dept"]) or "Unknown",
            "location": self._pick(
                text_row,
                ["office", "physicaldeliveryofficename", "physical_delivery_office_name", "location", "city"],
            ) or "Unknown",
            "manager": self._pick(text_row, ["managerdisplayname", "manager_display_name", "manager"]) or None,
            "source": "active_directory",
            "ad_object_guid": self._pick(text_row, ["objectguid", "object_guid", "guid"]) or None,
            "ad_sam_account_name": sam or None,
            "ad_user_principal_name": upn or None,
            "ad_enabled": 1 if enabled else 0,
            "ad_distinguished_name": self._pick(text_row, ["distinguishedname", "distinguished_name", "dn"]) or None,
            "ad_last_logon_at": self._pick(
                text_row,
                ["lastlogondate", "last_logon_date", "lastlogontimestamp", "last_logon_timestamp", "lastlogon"],
            ) or None,
            "ad_last_sync_at": now,
            "ad_disabled_flagged_at": now if not enabled else None,
        }

    def _match_ad_employee(self, conn: sqlite3.Connection, ad_user: dict) -> sqlite3.Row | None:
        candidates = [
            ("ad_object_guid", ad_user.get("ad_object_guid")),
            ("employee_id", ad_user.get("employee_id")),
            ("email", ad_user.get("email")),
            ("ad_user_principal_name", ad_user.get("ad_user_principal_name")),
            ("ad_sam_account_name", ad_user.get("ad_sam_account_name")),
        ]
        for column, value in candidates:
            if not value:
                continue
            found = conn.execute(
                f"SELECT * FROM employees WHERE lower({column}) = lower(?)",
                [value],
            ).fetchone()
            if found:
                return found
        return None

    def _create_employee_from_ad(self, conn: sqlite3.Connection, ad_user: dict) -> int:
        now = utc_now()
        data = {
            **ad_user,
            "status": "active",
            "start_date": today(),
            "termination_date": None,
            "admin_override": 0,
            "admin_notes": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            return insert_row(conn, "employees", data)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "AD user conflicts with an existing employee ID or email") from exc

    def _update_employee_from_ad(
        self,
        conn: sqlite3.Connection,
        employee: sqlite3.Row,
        ad_user: dict,
        stats: dict,
    ) -> int:
        previous_enabled = employee["ad_enabled"]
        update = {
            "source": "active_directory",
            "ad_object_guid": ad_user["ad_object_guid"],
            "ad_sam_account_name": ad_user["ad_sam_account_name"],
            "ad_user_principal_name": ad_user["ad_user_principal_name"],
            "ad_enabled": ad_user["ad_enabled"],
            "ad_distinguished_name": ad_user["ad_distinguished_name"],
            "ad_last_logon_at": ad_user["ad_last_logon_at"],
            "ad_last_sync_at": ad_user["ad_last_sync_at"],
            "ad_disabled_flagged_at": (
                employee["ad_disabled_flagged_at"] if ad_user["ad_enabled"] == 0 and employee["ad_disabled_flagged_at"] else ad_user["ad_disabled_flagged_at"]
            ),
            "updated_at": utc_now(),
        }
        if previous_enabled == 0 and ad_user["ad_enabled"] == 1:
            stats["reenabled_users"] += 1
        if employee["admin_override"]:
            stats["preserved_overrides"] += 1
        else:
            update.update(
                {
                    "name": ad_user["name"],
                    "email": ad_user["email"],
                    "department": ad_user["department"],
                    "location": ad_user["location"],
                    "manager": ad_user["manager"],
                }
            )
        update_row(conn, "employees", employee["id"], update)
        return int(employee["id"])

    def _create_access_from_import(
        self,
        conn: sqlite3.Connection,
        employee: sqlite3.Row,
        system: dict,
        run_id: int,
        access_level: str,
        access_type: str,
        actor: str,
    ) -> bool:
        existing = conn.execute(
            """
            SELECT id
            FROM access_records
            WHERE employee_id = ?
              AND system_id = ?
              AND access_level = ?
              AND access_type = ?
              AND status IN ('active', 'approved', 'unknown', 'removal_pending')
            """,
            [employee["id"], system["id"], access_level, access_type],
        ).fetchone()
        if existing:
            return False
        status = "removal_pending" if employee["status"] == "terminated" else "unknown"
        removal_due_at = (date.today() + timedelta(days=3)).isoformat() if status == "removal_pending" else None
        record_id = insert_row(
            conn,
            "access_records",
            {
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_level": access_level,
                "access_type": access_type,
                "status": status,
                "business_reason": "Imported account; business reason pending review.",
                "approved_by": None,
                "approved_at": None,
                "owner": system["owner"],
                "last_reviewed_at": None,
                "removal_due_at": removal_due_at,
                "removed_at": None,
                "removal_evidence": None,
                "expires_at": None,
                "evidence_url": None,
                "evidence_notes": "Created from imported account inventory.",
                "mfa_enabled": None,
                "last_rotated_at": None,
                "rotation_due_at": None,
                "notes": f"Created from import run {run_id}.",
                "source_import_run_id": run_id,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        self._audit(
            conn,
            actor,
            "Admin",
            "create_from_import",
            "access_record",
            record_id,
            f"Created access record from import for {employee['name']} in {system['name']}.",
            before=None,
            after={"import_run_id": run_id, "status": status},
        )
        return True

    def _match_employee(
        self, conn: sqlite3.Connection, employee_identifier: str, email: str, display_name: str
    ) -> sqlite3.Row | None:
        if employee_identifier:
            found = conn.execute(
                "SELECT * FROM employees WHERE lower(employee_id) = lower(?)",
                [employee_identifier],
            ).fetchone()
            if found:
                return found
        if email:
            found = conn.execute(
                "SELECT * FROM employees WHERE lower(email) = lower(?)",
                [email],
            ).fetchone()
            if found:
                return found
        if display_name:
            return conn.execute(
                "SELECT * FROM employees WHERE lower(name) = lower(?)",
                [display_name],
            ).fetchone()
        return None

    def _pick(self, row: dict, keys: list[str]) -> str:
        for key in keys:
            if row.get(key):
                return row[key]
        return ""

    def offboarding(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT e.id,
                           e.employee_id,
                           e.name,
                           e.email,
                           e.department,
                           e.location,
                           e.termination_date,
                           COUNT(ar.id) AS total_access_records,
                           SUM(CASE WHEN ar.status IN ('active', 'approved', 'unknown', 'removal_pending') THEN 1 ELSE 0 END) AS open_removals,
                           SUM(CASE WHEN ar.status = 'removed' THEN 1 ELSE 0 END) AS completed_removals
                    FROM employees e
                    LEFT JOIN access_records ar ON ar.employee_id = e.id
                    WHERE e.status = 'terminated'
                    GROUP BY e.id
                    ORDER BY open_removals DESC, e.termination_date DESC
                    """
                ).fetchall()
            )

    def audit_log(self) -> list[dict]:
        with self.session() as conn:
            return rows_to_dicts(
                conn.execute(
                    """
                    SELECT id, actor, role, action, entity_type, entity_id, summary, created_at
                    FROM audit_log
                    ORDER BY id DESC
                    LIMIT 200
                    """
                ).fetchall()
            )


ROLE_PERMISSIONS = {
    "Admin": {"create", "update", "review", "import"},
    "Reviewer": {"review"},
    "HR": {"create", "update"},
    "ReadOnly": set(),
}


def make_handler(store: Store, static_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AccessRegister/1.0"
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

        def log_message(self, format: str, *args) -> None:
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

        def _dispatch(self, method: str) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/"):
                    self._handle_api(method, parsed.path, parse_qs(parsed.query))
                elif method == "GET":
                    self._serve_static(parsed.path)
                else:
                    raise ApiError(405, "Method not allowed")
            except ApiError as exc:
                self._send_json({"error": exc.message}, exc.status)
            except Exception as exc:
                parsed_path = urlparse(self.path).path
                sys.stderr.write(
                    f"Unhandled {exc.__class__.__name__} while handling {method} {parsed_path}\n"
                )
                self._send_json({"error": "Internal server error"}, 500)

        def _handle_api(self, method: str, path: str, query: dict) -> None:
            role = self.headers.get("X-App-Role", "ReadOnly")
            actor = self.headers.get("X-App-Actor", role)

            if method == "GET" and path == "/api/bootstrap":
                self._send_json(self._bootstrap_payload(role))
                return
            if method == "GET" and path == "/api/summary":
                self._send_json(store.summary())
                return
            if method == "GET" and path == "/api/risk-findings":
                self._send_json({"riskFindings": store.risk_findings()})
                return
            if method == "GET" and path == "/api/disabled-access":
                self._send_json({"disabledAccess": store.disabled_access_queue()})
                return
            if method == "POST" and path == "/api/disabled-access/route-removal":
                self._require(role, "update", allowed_roles={"Admin", "HR"})
                self._send_json({"result": store.route_disabled_access_to_removal(actor, role)})
                return
            if method == "GET" and path == "/api/employees":
                self._send_json({"employees": store.list_employees()})
                return
            if method == "POST" and path == "/api/employees":
                self._require(role, "create", allowed_roles={"Admin", "HR"})
                self._send_json({"employee": store.create_employee(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path.startswith("/api/employees/"):
                employee_id = self._path_int(path, "/api/employees/")
                self._send_json(store.employee_detail(employee_id))
                return
            if method == "PATCH" and path.startswith("/api/employees/"):
                self._require(role, "update", allowed_roles={"Admin", "HR"})
                employee_id = self._path_int(path, "/api/employees/")
                payload = self._read_json()
                if role != "Admin" and {"admin_override", "admin_notes"} & set(payload):
                    raise ApiError(403, "Only Admin can change manual override settings")
                self._send_json({"employee": store.update_employee(employee_id, payload, actor, role)})
                return

            if method == "GET" and path == "/api/systems":
                self._send_json({"systems": store.list_systems()})
                return
            if method == "POST" and path == "/api/systems":
                self._require(role, "create", allowed_roles={"Admin"})
                self._send_json({"system": store.create_system(self._read_json(), actor, role)}, 201)
                return

            if method == "GET" and path == "/api/access-records":
                filters = {key: values[0] for key, values in query.items() if values and values[0]}
                self._send_json({"accessRecords": store.list_access_records(filters)})
                return
            if method == "POST" and path == "/api/access-records":
                self._require(role, "create", allowed_roles={"Admin"})
                self._send_json({"accessRecord": store.create_access_record(self._read_json(), actor, role)}, 201)
                return
            if method == "PATCH" and path.startswith("/api/access-records/"):
                record_id, suffix = self._record_path(path)
                if suffix == "/review":
                    self._require(role, "review", allowed_roles={"Admin", "Reviewer"})
                    self._send_json({"accessRecord": store.review_access_record(record_id, self._read_json(), actor, role)})
                else:
                    self._require(role, "update", allowed_roles={"Admin", "HR"})
                    self._send_json({"accessRecord": store.update_access_record(record_id, self._read_json(), actor, role)})
                return
            if method == "POST" and path.startswith("/api/access-records/") and path.endswith("/review"):
                record_id, _suffix = self._record_path(path)
                self._require(role, "review", allowed_roles={"Admin", "Reviewer"})
                self._send_json({"accessRecord": store.review_access_record(record_id, self._read_json(), actor, role)})
                return

            if method == "GET" and path == "/api/imports":
                self._send_json({"imports": store.list_imports()})
                return
            if method == "POST" and path == "/api/imports/accounts":
                self._require(role, "import", allowed_roles={"Admin"})
                self._send_json({"importRun": store.import_accounts(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/ad-sync-runs":
                self._send_json({"adSyncRuns": store.list_ad_sync_runs()})
                return
            if method == "POST" and path == "/api/ad/sync":
                self._require(role, "import", allowed_roles={"Admin"})
                self._send_json({"adSyncRun": store.sync_ad_users(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/ad-sync-settings":
                self._require(role, "update", allowed_roles={"Admin"})
                self._send_json({"adSyncSettings": store.get_ad_sync_settings(include_payload=True)})
                return
            if method == "POST" and path == "/api/ad-sync-settings":
                self._require(role, "update", allowed_roles={"Admin"})
                self._send_json({"adSyncSettings": store.update_ad_sync_settings(self._read_json(), actor, role)})
                return
            if method == "POST" and path == "/api/ad/run-scheduled":
                self._require(role, "import", allowed_roles={"Admin"})
                payload = self._read_json()
                self._send_json({"result": store.run_scheduled_ad_sync(actor, role, force=bool(parse_bool(payload.get("force"))) )})
                return

            if method == "GET" and path == "/api/access-requests":
                self._send_json({"accessRequests": store.list_access_requests()})
                return
            if method == "POST" and path == "/api/access-requests":
                self._require(role, "create", allowed_roles={"Admin", "HR"})
                self._send_json({"accessRequest": store.create_access_request(self._read_json(), actor, role)}, 201)
                return
            if method == "POST" and path.startswith("/api/access-requests/") and path.endswith("/decision"):
                self._require(role, "review", allowed_roles={"Admin", "Reviewer"})
                request_id = self._path_int(path.removesuffix("/decision"), "/api/access-requests/")
                self._send_json({"accessRequest": store.decide_access_request(request_id, self._read_json(), actor, role)})
                return

            if method == "GET" and path == "/api/review-campaigns":
                self._send_json({"reviewCampaigns": store.list_review_campaigns()})
                return
            if method == "POST" and path == "/api/review-campaigns":
                self._require(role, "review", allowed_roles={"Admin", "Reviewer"})
                self._send_json({"reviewCampaign": store.create_review_campaign(self._read_json(), actor, role)}, 201)
                return
            if method == "PATCH" and path.startswith("/api/review-campaigns/"):
                self._require(role, "review", allowed_roles={"Admin", "Reviewer"})
                campaign_id = self._path_int(path, "/api/review-campaigns/")
                self._send_json({"reviewCampaign": store.update_review_campaign(campaign_id, self._read_json(), actor, role)})
                return

            if method == "GET" and path == "/api/notifications":
                self._send_json({"notifications": store.list_notifications()})
                return
            if method == "PATCH" and path.startswith("/api/notifications/"):
                self._require(role, "update", allowed_roles={"Admin", "Reviewer", "HR"})
                notification_id = self._path_int(path, "/api/notifications/")
                self._send_json({"notification": store.acknowledge_notification(notification_id, actor, role)})
                return

            if method == "GET" and path == "/api/shared-accounts":
                self._send_json({"sharedAccounts": store.list_shared_accounts()})
                return
            if method == "POST" and path == "/api/shared-accounts":
                self._require(role, "create", allowed_roles={"Admin"})
                self._send_json({"sharedAccount": store.create_shared_account(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/physical-credentials":
                self._send_json({"physicalCredentials": store.list_physical_credentials()})
                return
            if method == "POST" and path == "/api/physical-credentials":
                self._require(role, "create", allowed_roles={"Admin", "HR"})
                self._send_json({"physicalCredential": store.create_physical_credential(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/connectors":
                self._send_json({"connectors": store.list_connectors()})
                return
            if method == "POST" and path == "/api/connectors":
                self._require(role, "create", allowed_roles={"Admin"})
                self._send_json({"connector": store.create_connector(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/owner-dashboard":
                owner = query.get("owner", [None])[0]
                self._send_json({"ownerDashboard": store.owner_dashboard(owner)})
                return
            if method == "GET" and path == "/api/backups":
                self._send_json({"backups": store.list_backups()})
                return
            if method == "POST" and path == "/api/backups/run":
                self._require(role, "update", allowed_roles={"Admin"})
                self._send_json({"backup": store.run_backup(self._read_json(), actor, role)}, 201)
                return
            if method == "GET" and path == "/api/auth-settings":
                self._send_json({"authSettings": store.get_auth_settings()})
                return
            if method == "POST" and path == "/api/auth-settings":
                self._require(role, "update", allowed_roles={"Admin"})
                self._send_json({"authSettings": store.update_auth_settings(self._read_json(), actor, role)})
                return

            if method == "GET" and path == "/api/offboarding":
                self._send_json({"offboarding": store.offboarding()})
                return
            if method == "GET" and path == "/api/audit-log.csv":
                self._send_text(store.audit_log_csv(), "text/csv; charset=utf-8")
                return
            if method == "GET" and path == "/api/audit-log":
                self._send_json({"audit": store.audit_log()})
                return

            raise ApiError(404, "API route not found")

        def _bootstrap_payload(self, role: str) -> dict:
            return {
                "summary": store.summary(),
                "employees": store.list_employees(),
                "systems": store.list_systems(),
                "accessRecords": store.list_access_records({}),
                "imports": store.list_imports(),
                "adSyncRuns": store.list_ad_sync_runs(),
                "adSyncSettings": store.get_ad_sync_settings(include_payload=role == "Admin"),
                "accessRequests": store.list_access_requests(),
                "disabledAccess": store.disabled_access_queue(),
                "riskFindings": store.risk_findings(),
                "notifications": store.list_notifications(),
                "reviewCampaigns": store.list_review_campaigns(),
                "sharedAccounts": store.list_shared_accounts(),
                "physicalCredentials": store.list_physical_credentials(),
                "connectors": store.list_connectors(),
                "ownerDashboard": store.owner_dashboard(),
                "backups": store.list_backups(),
                "authSettings": store.get_auth_settings(),
                "offboarding": store.offboarding(),
                "audit": store.audit_log(),
            }

        def _record_path(self, path: str) -> tuple[int, str]:
            rest = path.removeprefix("/api/access-records/")
            parts = rest.split("/", 1)
            try:
                record_id = int(parts[0])
            except ValueError as exc:
                raise ApiError(400, "Invalid access record ID") from exc
            suffix = "" if len(parts) == 1 else "/" + parts[1]
            return record_id, suffix

        def _path_int(self, path: str, prefix: str) -> int:
            value = path.removeprefix(prefix).split("/", 1)[0]
            try:
                return int(value)
            except ValueError as exc:
                raise ApiError(400, "Invalid numeric ID") from exc

        def _require(self, role: str, permission: str, allowed_roles: set[str]) -> None:
            if role not in allowed_roles or permission not in ROLE_PERMISSIONS.get(role, set()):
                raise ApiError(403, f"{role} role cannot perform this action")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
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
            self.send_header("X-Content-Type-Options", "nosniff")

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


def start_scheduler(store: Store) -> None:
    def loop() -> None:
        while True:
            try:
                store.run_scheduled_ad_sync(actor="Scheduler", role="Admin", force=False)
            except Exception as error:
                sys.stderr.write(f"Scheduled AD sync failed: {error}\n")
            time.sleep(60)

    thread = threading.Thread(target=loop, name="access-register-scheduler", daemon=True)
    thread.start()


def run(host: str = "127.0.0.1", port: int = 8087, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    store = Store(db_path)
    store.init(seed=True)
    if os.environ.get("ACCESS_REGISTER_SCHEDULER", "1") != "0":
        start_scheduler(store)
    handler = make_handler(store, STATIC_DIR)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Access Register running at http://{host}:{port}")
    print(f"SQLite database: {Path(db_path).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Access Register.")
    finally:
        server.server_close()


if __name__ == "__main__":
    host = os.environ.get("ACCESS_REGISTER_HOST", "127.0.0.1")
    port = int(os.environ.get("ACCESS_REGISTER_PORT", "8087"))
    db_path = Path(os.environ.get("ACCESS_REGISTER_DB", DEFAULT_DB_PATH))
    run(host, port, db_path)
