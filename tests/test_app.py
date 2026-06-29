import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ApiError, Store, validate_startup_security  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "gatewatch.db"
        self.store = Store(self.db_path)
        self.store.init()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_edit_checklist_delete_employee_flow_persists_to_sqlite(self):
        created = self.store.create_employee(
            {
                "employee_id": "E-1001",
                "name": "Avery Morgan",
                "email": "avery.morgan@example.com",
                "department": "Operations",
                "title": "Operations Manager",
                "location": "HQ",
                "manager": "Dana Chen",
                "request_source": "HR",
                "access_needed": "Email, VPN, payroll",
                "request_received": True,
                "manager_approved": True,
            },
            actor="Unit Test",
        )

        self.assertEqual(created["name"], "Avery Morgan")
        self.assertEqual(created["request_received"], 1)
        self.assertEqual(created["manager_approved"], 1)
        self.assertEqual(created["it_provisioned"], 0)
        self.assertTrue(self.db_path.exists())
        self.assertEqual(self.store.summary()["inProgress"], 1)

        reopened = Store(self.db_path)
        reopened.init()
        stored = reopened.get_employee(created["id"])
        self.assertEqual(stored["email"], "avery.morgan@example.com")

        updated = reopened.update_employee(
            created["id"],
            {
                "title": "Senior Operations Manager",
                "it_provisioned": True,
                "employee_notified": True,
                "notes": "Access granted after manager approval.",
            },
            actor="Unit Test",
        )

        self.assertEqual(updated["title"], "Senior Operations Manager")
        self.assertEqual(updated["it_provisioned"], 1)
        self.assertEqual(updated["employee_notified"], 1)
        self.assertEqual(reopened.summary()["inProgress"], 0)

        deleted = reopened.delete_employee(created["id"], actor="Unit Test")
        self.assertEqual(deleted["employee_id"], "E-1001")
        with self.assertRaises(ApiError) as context:
            reopened.get_employee(created["id"])
        self.assertEqual(context.exception.status, 404)
        self.assertEqual(reopened.summary()["total"], 0)

        audit = reopened.audit_log()
        self.assertEqual([entry["action"] for entry in audit[:3]], ["delete", "update", "create"])

    def test_validation_and_uniqueness_errors_are_clear(self):
        self.store.create_employee(
            {
                "employee_id": "E-1001",
                "name": "Avery Morgan",
                "email": "avery.morgan@example.com",
            }
        )

        with self.assertRaises(ApiError) as duplicate:
            self.store.create_employee(
                {
                    "employee_id": "E-1001",
                    "name": "Avery Morgan Copy",
                    "email": "avery.copy@example.com",
                }
            )
        with self.assertRaises(ApiError) as bad_email:
            self.store.create_employee(
                {
                    "employee_id": "E-1002",
                    "name": "Bad Email",
                    "email": "not-an-email",
                }
            )
        with self.assertRaises(ApiError) as bad_status:
            self.store.create_employee(
                {
                    "employee_id": "E-1003",
                    "name": "Bad Status",
                    "email": "bad.status@example.com",
                    "status": "pending",
                }
            )

        self.assertEqual(duplicate.exception.status, 409)
        self.assertEqual(bad_email.exception.status, 400)
        self.assertIn("plain email", bad_email.exception.message)
        self.assertIn("active, disabled, or terminated", bad_status.exception.message)

    def test_disabled_status_and_legacy_status_check_migration(self):
        legacy_db = Path(self.tempdir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_db)
        try:
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
                CREATE TABLE audit_log (
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
                INSERT INTO employees (
                    employee_id, name, email, status, created_at, updated_at
                )
                VALUES ('E-LEGACY', 'Legacy User', 'legacy@example.com', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
                """
            )
        finally:
            conn.close()

        migrated = Store(legacy_db)
        migrated.init()
        disabled = migrated.create_employee(
            {
                "employee_id": "E-DISABLED",
                "name": "Disabled User",
                "email": "disabled@example.com",
                "status": "disabled",
            }
        )

        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(migrated.summary()["disabled"], 1)
        self.assertEqual(migrated.get_employee(1)["name"], "Legacy User")

    def test_entra_sync_creates_updates_and_tracks_disabled_users(self):
        users = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "displayName": "Jordan Lee",
                "mail": "jordan.lee@example.com",
                "userPrincipalName": "jordan.lee@example.com",
                "department": "Finance",
                "jobTitle": "Controller",
                "officeLocation": "HQ",
                "accountEnabled": True,
                "employeeId": "E-4001",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "displayName": "Taylor Reed",
                "mail": None,
                "userPrincipalName": "taylor.reed@example.com",
                "department": "IT",
                "jobTitle": "Analyst",
                "officeLocation": "Remote",
                "accountEnabled": False,
                "employeeId": "E-4002",
            },
        ]

        result = self.store.sync_entra_users(users, actor="Sync Test")
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["disabled"], 1)
        self.assertEqual(result["skipped"], 0)

        disabled = self.store.list_employees("taylor")[0]
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(disabled["entra_account_enabled"], 0)
        self.assertEqual(disabled["request_source"], "Entra ID")

        repeated = self.store.sync_entra_users(users, actor="Sync Test")
        self.assertEqual(repeated["unchanged"], 2)
        self.assertEqual(repeated["updated"], 0)

        users[1]["accountEnabled"] = True
        users[1]["displayName"] = "Taylor Reed-Updated"
        updated = self.store.sync_entra_users(users, actor="Sync Test")
        self.assertEqual(updated["updated"], 1)
        employee = self.store.list_employees("reed-updated")[0]
        self.assertEqual(employee["status"], "active")
        self.assertEqual(employee["entra_account_enabled"], 1)

    def test_search_summary_sqlite_pragmas_and_audit_csv(self):
        employee = self.store.create_employee(
            {
                "employee_id": "E-2001",
                "name": '=HYPERLINK("http://example.invalid","Avery")',
                "email": "formula.safe@example.com",
                "department": "Finance",
                "request_source": "Manager",
                "access_needed": "Shared drive",
            },
            actor="=SUM(1,1)",
        )

        self.assertEqual(self.store.summary()["total"], 1)
        self.assertEqual(self.store.summary()["active"], 1)
        self.assertEqual(self.store.list_employees("shared drive")[0]["id"], employee["id"])

        with self.store.session() as conn:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

        rows = list(csv.DictReader(io.StringIO(self.store.audit_log_csv())))
        self.assertEqual(rows[0]["actor"], "'=SUM(1,1)")

    def test_startup_security_defaults_to_loopback_only(self):
        previous = os.environ.pop("GATEWATCH_ALLOW_INSECURE_NETWORK", None)

        def restore():
            if previous is None:
                os.environ.pop("GATEWATCH_ALLOW_INSECURE_NETWORK", None)
            else:
                os.environ["GATEWATCH_ALLOW_INSECURE_NETWORK"] = previous

        self.addCleanup(restore)

        validate_startup_security("127.0.0.1")
        validate_startup_security("localhost")
        with self.assertRaises(RuntimeError):
            validate_startup_security("0.0.0.0")
        os.environ["GATEWATCH_ALLOW_INSECURE_NETWORK"] = "1"
        validate_startup_security("0.0.0.0")


class HttpTests(unittest.TestCase):
    def setUp(self):
        from app import GatewatchServer, STATIC_DIR, make_handler

        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tempdir.name) / "http.db")
        self.store.init()
        handler = make_handler(self.store, STATIC_DIR)
        self.server = GatewatchServer(("127.0.0.1", 0), handler)
        self.addCleanup(self.server.server_close)
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, method, path, body=None, expected_error=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = response.read().decode("utf-8")
                if expected_error:
                    raise AssertionError(f"{method} {path} succeeded")
                if response.headers.get_content_type() == "application/json":
                    return response.status, json.loads(payload) if payload else {}
                return response.status, payload
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8")
            if expected_error and error.code == expected_error:
                return error.code, json.loads(details)
            raise

    def test_http_employee_crud_and_static_ui(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Employee Tracker", html)
        self.assertIn("Access Request Flow", html)
        self.assertIn("Microsoft Entra", html)
        self.assertIn('data-step="request_received"', html)
        self.assertNotIn('type="checkbox"', html)

        status, created = self.request(
            "POST",
            "/api/employees",
            {
                "employee_id": "E-3001",
                "name": "Riley Brooks",
                "email": "riley.brooks@example.com",
                "request_source": "Manager",
                "access_needed": "VPN and laptop",
                "request_received": True,
            },
        )
        employee_id = created["employee"]["id"]
        self.assertEqual(status, 201)

        _, bootstrap = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["summary"]["total"], 1)
        self.assertEqual(bootstrap["summary"]["inProgress"], 1)
        self.assertIn("auth", bootstrap)
        self.assertFalse(bootstrap["auth"]["configured"])

        _, updated = self.request(
            "PATCH",
            f"/api/employees/{employee_id}",
            {"it_provisioned": True, "employee_notified": True},
        )
        self.assertEqual(updated["employee"]["it_provisioned"], 1)
        self.assertEqual(updated["employee"]["employee_notified"], 1)

        _, deleted = self.request("DELETE", f"/api/employees/{employee_id}")
        self.assertEqual(deleted["employee"]["name"], "Riley Brooks")

        error_status, error = self.request("GET", f"/api/employees/{employee_id}", expected_error=404)
        self.assertEqual(error_status, 404)
        self.assertIn("not found", error["error"])

    def test_auth_status_and_entra_sync_http_route(self):
        _, auth = self.request("GET", "/api/auth/status")
        self.assertFalse(auth["entra"]["configured"])
        self.assertFalse(auth["entra"]["graphConfigured"])

        graph_users = [
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "displayName": "Morgan North",
                "mail": "morgan.north@example.com",
                "userPrincipalName": "morgan.north@example.com",
                "department": "Security",
                "jobTitle": "Engineer",
                "officeLocation": "HQ",
                "accountEnabled": False,
                "employeeId": "E-5001",
            }
        ]

        with mock.patch.dict(
            os.environ,
            {
                "GATEWATCH_ENTRA_TENANT_ID": "example-tenant",
                "GATEWATCH_ENTRA_CLIENT_ID": "example-client",
                "GATEWATCH_ENTRA_CLIENT_SECRET": "example-secret",
                "GATEWATCH_ENTRA_REDIRECT_URI": f"{self.base_url}/auth/entra/callback",
            },
        ), mock.patch("app.fetch_graph_users", return_value=graph_users):
            _, configured = self.request("GET", "/api/auth/status")
            self.assertTrue(configured["entra"]["configured"])
            self.assertTrue(configured["entra"]["ssoConfigured"])
            self.assertTrue(configured["entra"]["graphConfigured"])

            status, payload = self.request("POST", "/api/entra/sync")
            self.assertEqual(status, 200)
            self.assertEqual(payload["sync"]["created"], 1)
            self.assertEqual(payload["sync"]["disabled"], 1)

        employees = self.store.list_employees("morgan")
        self.assertEqual(len(employees), 1)
        self.assertEqual(employees[0]["status"], "disabled")

    def test_cross_origin_write_requests_are_rejected(self):
        status, error = self.request(
            "POST",
            "/api/employees",
            {
                "employee_id": "E-CSRF",
                "name": "Cross Origin",
                "email": "cross.origin@example.com",
            },
            expected_error=403,
            headers={"Origin": "https://evil.example"},
        )

        self.assertEqual(status, 403)
        self.assertIn("Cross-origin", error["error"])
        self.assertEqual(self.store.summary()["total"], 0)


if __name__ == "__main__":
    unittest.main()
