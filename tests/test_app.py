import csv
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    ApiError,
    SESSION_COOKIE,
    Store,
    group_matches_admin,
    signed_payload,
    validate_startup_security,
)


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
        with mock.patch.dict(
            os.environ,
            {
                "GATEWATCH_AUTH_MODE": "trusted_proxy",
                "GATEWATCH_PROXY_SECRET": "",
                "ACCESS_REGISTER_PROXY_SECRET": "",
                "GATEWATCH_ALLOW_INSECURE_NETWORK": "",
            },
        ):
            with self.assertRaises(RuntimeError):
                validate_startup_security("0.0.0.0")
        with mock.patch.dict(
            os.environ,
            {
                "GATEWATCH_AUTH_MODE": "trusted_proxy",
                "GATEWATCH_PROXY_SECRET": "short",
                "ACCESS_REGISTER_PROXY_SECRET": "",
                "GATEWATCH_ALLOW_INSECURE_NETWORK": "",
            },
        ):
            with self.assertRaises(RuntimeError):
                validate_startup_security("0.0.0.0")
        with mock.patch.dict(
            os.environ,
            {
                "GATEWATCH_AUTH_MODE": "trusted_proxy",
                "GATEWATCH_PROXY_SECRET": "proxy-secret-value",
                "ACCESS_REGISTER_PROXY_SECRET": "",
                "GATEWATCH_ALLOW_INSECURE_NETWORK": "",
            },
        ):
            validate_startup_security("0.0.0.0")

    def test_domain_admin_group_matching_accepts_configured_group_identifiers(self):
        self.assertTrue(group_matches_admin({"displayName": "Domain Admins"}))
        self.assertTrue(group_matches_admin({"onPremisesSamAccountName": "Domain Admins"}))
        with mock.patch.dict(os.environ, {"GATEWATCH_ADMIN_GROUP_CANONICAL": "group-object-id"}):
            self.assertTrue(group_matches_admin({"id": "group-object-id"}))
            self.assertFalse(group_matches_admin({"displayName": "Domain Admins"}))

    def test_change_requests_are_reviewed_before_applying_employee_updates(self):
        employee = self.store.create_employee(
            {
                "employee_id": "E-APPROVE",
                "name": "Approval User",
                "email": "approval.user@example.com",
                "department": "Operations",
            },
            actor="Requester",
        )

        request = self.store.create_change_request(
            employee["id"],
            {
                "department": "IT",
                "title": "Systems Analyst",
                "manager_approved": True,
            },
            actor="Viewer User",
        )

        self.assertEqual(request["status"], "pending")
        self.assertEqual(request["payload"]["department"], "IT")
        self.assertEqual(self.store.get_employee(employee["id"])["department"], "Operations")

        approved = self.store.review_change_request(request["id"], approve=True, actor="Domain Admin")
        self.assertEqual(approved["status"], "approved")
        updated = self.store.get_employee(employee["id"])
        self.assertEqual(updated["department"], "IT")
        self.assertEqual(updated["title"], "Systems Analyst")
        self.assertEqual(updated["manager_approved"], 1)

        rejected_request = self.store.create_change_request(
            employee["id"],
            {"title": "Should Not Apply"},
            actor="Viewer User",
        )
        rejected = self.store.review_change_request(rejected_request["id"], approve=False, actor="Domain Admin")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.store.get_employee(employee["id"])["title"], "Systems Analyst")

        audit_actions = [entry["action"] for entry in self.store.audit_log()]
        self.assertIn("request_change", audit_actions)
        self.assertIn("approve_change_request", audit_actions)
        self.assertIn("reject_change_request", audit_actions)

    def test_access_profile_fields_are_configurable_and_audited(self):
        seeded = self.store.list_access_fields()
        self.assertGreaterEqual(len(seeded), 10)
        self.assertIn("software_access", {field["key"] for field in seeded})
        self.assertIn("Systems Access", {field["section"] for field in seeded})

        created = self.store.create_access_field(
            {
                "label": "Core Banking Role",
                "section": "Systems Access",
                "field_type": "select",
                "options": ["Teller", "Supervisor", "Read Only"],
                "required": True,
                "sort_order": 211,
            },
            actor="Domain Admin",
        )
        self.assertEqual(created["key"], "core_banking_role")
        self.assertEqual(created["field_type"], "select")
        self.assertTrue(created["required"])
        self.assertEqual(created["options"], ["Teller", "Supervisor", "Read Only"])

        updated = self.store.update_access_field(
            created["id"],
            {"label": "Core Banking Profile", "options": ["Teller", "Manager"]},
            actor="Domain Admin",
        )
        self.assertEqual(updated["label"], "Core Banking Profile")
        self.assertEqual(updated["options"], ["Teller", "Manager"])

        removed = self.store.delete_access_field(created["id"], actor="Domain Admin")
        self.assertFalse(removed["active"])
        active_keys = {field["key"] for field in self.store.list_access_fields(include_inactive=False)}
        self.assertNotIn("core_banking_role", active_keys)

        audit_actions = [entry["action"] for entry in self.store.audit_log()]
        self.assertIn("create_access_field", audit_actions)
        self.assertIn("update_access_field", audit_actions)
        self.assertIn("delete_access_field", audit_actions)

    def test_access_profile_persists_and_change_request_approval_applies_it(self):
        employee = self.store.create_employee(
            {
                "employee_id": "E-PROFILE",
                "name": "Profile User",
                "email": "profile.user@example.com",
                "access_profile": {
                    "software_access": "VPN\nCore banking",
                    "corporate_card": True,
                    "branch": "Downtown",
                },
            },
            actor="Requester",
        )

        stored = self.store.get_employee(employee["id"])
        self.assertEqual(stored["access_profile"]["software_access"], "VPN\nCore banking")
        self.assertTrue(stored["access_profile"]["corporate_card"])

        request = self.store.create_change_request(
            employee["id"],
            {
                "access_profile": {
                    "software_access": "VPN\nCore banking\nWire access",
                    "corporate_card": False,
                    "branch": "HQ",
                }
            },
            actor="Viewer User",
        )
        self.assertIn("access_profile", request["payload"])
        self.assertNotIn("access_profile_json", request["payload"])
        self.assertEqual(request["payload"]["access_profile"]["branch"], "HQ")
        self.assertEqual(self.store.get_employee(employee["id"])["access_profile"]["branch"], "Downtown")

        approved = self.store.review_change_request(request["id"], approve=True, actor="Domain Admin")
        self.assertEqual(approved["status"], "approved")
        updated = self.store.get_employee(employee["id"])
        self.assertEqual(updated["access_profile"]["branch"], "HQ")
        self.assertFalse(updated["access_profile"]["corporate_card"])
        self.assertIn("Wire access", updated["access_profile"]["software_access"])


class HttpTests(unittest.TestCase):
    _next_port = 19087

    def setUp(self):
        from app import GatewatchServer, STATIC_DIR, make_handler

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = Store(Path(self.tempdir.name) / "http.db")
        self.store.init()
        handler = make_handler(self.store, STATIC_DIR)
        self.server = self.make_server(GatewatchServer, handler)
        self.addCleanup(self.server.server_close)
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.wait_for_server()

    @classmethod
    def make_server(cls, server_cls, handler):
        last_error = None
        for _ in range(100):
            port = cls._next_port
            cls._next_port += 1
            try:
                return server_cls(("127.0.0.1", port), handler)
            except OSError as error:
                last_error = error
        if last_error:
            raise last_error
        return server_cls(("127.0.0.1", 0), handler)

    def session_headers(self, *, can_modify=True, name="Domain Admin", email="domain.admin@gcefcu.org"):
        session = signed_payload(
            {
                "sub": "test-user",
                "tid": "test-tenant",
                "name": name,
                "email": email,
                "can_modify_employees": can_modify,
                "admin_group": "gcefcu.org/Users/Domain Admins",
                "groups_checked_at": "2026-06-29T00:00:00Z",
                "exp": time.time() + 3600,
            }
        )
        return {"Cookie": f"{SESSION_COOKIE}={session}"}

    def trusted_proxy_headers(
        self,
        *,
        secret="proxy-secret-value",
        name="Domain Admin",
        email="domain.admin@gcefcu.org",
        groups="Domain Admins",
    ):
        return {
            "X-Gatewatch-Proxy-Secret": secret,
            "X-Remote-User": email,
            "X-Remote-Name": name,
            "X-Remote-Email": email,
            "X-Remote-Groups": groups,
            "X-Remote-Tenant": "test-tenant",
        }

    def wait_for_server(self):
        deadline = time.time() + 45
        last_error = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.05)
        raise AssertionError(f"HTTP test server did not become ready: {last_error}")

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
        attempts = 3
        for attempt in range(attempts):
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
            except urllib.error.URLError as error:
                if not isinstance(error.reason, TimeoutError) or attempt + 1 >= attempts:
                    raise
                time.sleep(0.1)
            except TimeoutError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.1)
        raise AssertionError(f"{method} {path} did not complete")

    def test_http_employee_crud_and_static_ui(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Employee Tracker", html)
        self.assertIn("Activity Log", html)
        self.assertIn("profilesTab", html)
        self.assertIn("profilesView", html)
        self.assertIn("logsTab", html)
        self.assertIn(">Logs<", html)
        self.assertIn("Key Fob ID", html)
        self.assertIn("configurationTab", html)
        self.assertIn("accessProfileFields", html)
        self.assertIn("accessFieldForm", html)
        self.assertIn("terminateButton", html)
        self.assertIn("Custom Fields", html)
        self.assertIn("Change Requests", html)
        self.assertIn("Access Request Flow", html)
        self.assertIn("Microsoft Entra", html)
        self.assertIn('data-step="request_received"', html)
        self.assertIn('name="allowInsecureNetwork" type="checkbox"', html)

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
                "access_profile": {
                    "software_access": "VPN",
                    "branch": "HQ",
                    "corporate_card": True,
                },
            },
        )
        employee_id = created["employee"]["id"]
        self.assertEqual(status, 201)
        self.assertEqual(created["employee"]["access_profile"]["software_access"], "VPN")
        self.assertTrue(created["employee"]["access_profile"]["corporate_card"])

        _, bootstrap = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["summary"]["total"], 1)
        self.assertEqual(bootstrap["summary"]["inProgress"], 1)
        self.assertEqual(bootstrap["employees"][0]["access_profile"]["branch"], "HQ")
        self.assertIn("accessFields", bootstrap)
        self.assertIn("software_access", {field["key"] for field in bootstrap["accessFields"]})
        self.assertIn("auth", bootstrap)
        self.assertFalse(bootstrap["auth"]["configured"])
        self.assertFalse(bootstrap["auth"]["permissions"]["canModifyEmployees"])

        admin_headers = self.session_headers(name="Riley Admin", email="riley.admin@gcefcu.org")

        _, updated = self.request(
            "PATCH",
            f"/api/employees/{employee_id}",
            {"it_provisioned": True, "employee_notified": True},
            headers=admin_headers,
        )
        self.assertEqual(updated["employee"]["it_provisioned"], 1)
        self.assertEqual(updated["employee"]["employee_notified"], 1)

        _, deleted = self.request("DELETE", f"/api/employees/{employee_id}", headers=admin_headers)
        self.assertEqual(deleted["employee"]["name"], "Riley Brooks")

        error_status, error = self.request("GET", f"/api/employees/{employee_id}", expected_error=404)
        self.assertEqual(error_status, 404)
        self.assertIn("not found", error["error"])

        _, audit = self.request("GET", "/api/audit-log")
        self.assertEqual(audit["audit"][0]["actor"], "Riley Admin (riley.admin@gcefcu.org)")

    def test_http_access_field_catalog_requires_domain_admin_mutation(self):
        viewer_headers = self.session_headers(can_modify=False, name="Viewer User", email="viewer@gcefcu.org")
        admin_headers = self.session_headers(name="Catalog Admin", email="catalog.admin@gcefcu.org")

        _, catalog = self.request("GET", "/api/access-fields")
        self.assertIn("software_access", {field["key"] for field in catalog["accessFields"]})

        viewer_status, viewer_error = self.request(
            "POST",
            "/api/access-fields",
            {
                "label": "Wire Transfer Access",
                "section": "Systems Access",
                "fieldType": "textarea",
            },
            expected_error=403,
            headers=viewer_headers,
        )
        self.assertEqual(viewer_status, 403)
        self.assertIn("Domain Admins", viewer_error["error"])

        create_status, created = self.request(
            "POST",
            "/api/access-fields",
            {
                "label": "Wire Transfer Access",
                "section": "Systems Access",
                "fieldType": "textarea",
                "required": True,
                "sortOrder": 215,
            },
            headers=admin_headers,
        )
        field = created["accessField"]
        self.assertEqual(create_status, 201)
        self.assertEqual(field["key"], "wire_transfer_access")
        self.assertTrue(field["required"])

        _, updated = self.request(
            "PATCH",
            f"/api/access-fields/{field['id']}",
            {"label": "Wire Access Notes", "required": False},
            headers=admin_headers,
        )
        self.assertEqual(updated["accessField"]["label"], "Wire Access Notes")
        self.assertFalse(updated["accessField"]["required"])

        _, employee = self.request(
            "POST",
            "/api/employees",
            {
                "employee_id": "E-WIRE",
                "name": "Wire User",
                "email": "wire.user@example.com",
                "access_profile": {"wire_transfer_access": "Limit: $5,000"},
            },
        )
        self.assertEqual(employee["employee"]["access_profile"]["wire_transfer_access"], "Limit: $5,000")

        _, deleted = self.request(
            "DELETE",
            f"/api/access-fields/{field['id']}",
            headers=admin_headers,
        )
        self.assertFalse(deleted["accessField"]["active"])

        _, refreshed = self.request("GET", "/api/access-fields")
        inactive = next(item for item in refreshed["accessFields"] if item["id"] == field["id"])
        self.assertFalse(inactive["active"])

    def test_non_admin_update_creates_change_request_for_admin_approval(self):
        _, created = self.request(
            "POST",
            "/api/employees",
            {
                "employee_id": "E-LOCKED",
                "name": "Locked Employee",
                "email": "locked.employee@example.com",
            },
        )
        employee_id = created["employee"]["id"]
        viewer_headers = self.session_headers(can_modify=False, name="Viewer User", email="viewer@gcefcu.org")

        request_status, request_payload = self.request(
            "PATCH",
            f"/api/employees/{employee_id}",
            {
                "title": "Needs Approval",
                "manager_approved": True,
                "access_profile": {"software_access": "VPN"},
            },
        )
        viewer_request_status, viewer_request_payload = self.request(
            "PATCH",
            f"/api/employees/{employee_id}",
            {"department": "Finance"},
            headers=viewer_headers,
        )
        empty_status, empty_error = self.request(
            "PATCH",
            f"/api/employees/{employee_id}",
            {},
            expected_error=400,
            headers=viewer_headers,
        )
        delete_status, delete_error = self.request(
            "DELETE",
            f"/api/employees/{employee_id}",
            expected_error=403,
            headers=viewer_headers,
        )
        sync_status, sync_error = self.request(
            "POST",
            "/api/entra/sync",
            expected_error=403,
            headers=viewer_headers,
        )

        self.assertEqual(request_status, 202)
        self.assertEqual(viewer_request_status, 202)
        self.assertEqual(request_payload["changeRequest"]["status"], "pending")
        self.assertEqual(request_payload["changeRequest"]["payload"]["access_profile"]["software_access"], "VPN")
        self.assertEqual(viewer_request_payload["changeRequest"]["requested_by"], "Viewer User (viewer@gcefcu.org)")
        self.assertEqual(empty_status, 400)
        self.assertEqual(delete_status, 403)
        self.assertEqual(sync_status, 403)
        self.assertIn("No employee fields", empty_error["error"])
        self.assertIn("Domain Admins", delete_error["error"])
        self.assertIn("Domain Admins", sync_error["error"])
        self.assertEqual(self.store.get_employee(employee_id)["title"], "")

        _, local_bootstrap = self.request("GET", "/api/bootstrap")
        self.assertEqual(len(local_bootstrap["changeRequests"]), 1)
        self.assertEqual(local_bootstrap["changeRequests"][0]["requested_by"], "Local user")

        _, viewer_bootstrap = self.request("GET", "/api/bootstrap", headers=viewer_headers)
        self.assertEqual(len(viewer_bootstrap["changeRequests"]), 1)
        self.assertEqual(viewer_bootstrap["changeRequests"][0]["requested_by"], "Viewer User (viewer@gcefcu.org)")

        _, admin_queue = self.request(
            "GET",
            "/api/change-requests",
            headers=self.session_headers(name="Approving Admin", email="approver@gcefcu.org"),
        )
        self.assertEqual(len(admin_queue["changeRequests"]), 2)

        _, viewer_queue = self.request("GET", "/api/change-requests", headers=viewer_headers)
        self.assertEqual(len(viewer_queue["changeRequests"]), 1)
        self.assertEqual(viewer_queue["changeRequests"][0]["requested_by"], "Viewer User (viewer@gcefcu.org)")

        denied_review_status, denied_review_error = self.request(
            "POST",
            f"/api/change-requests/{request_payload['changeRequest']['id']}/approve",
            expected_error=403,
            headers=viewer_headers,
        )
        self.assertEqual(denied_review_status, 403)
        self.assertIn("Domain Admins", denied_review_error["error"])

        approve_status, approved = self.request(
            "POST",
            f"/api/change-requests/{request_payload['changeRequest']['id']}/approve",
            headers=self.session_headers(name="Approving Admin", email="approver@gcefcu.org"),
        )
        self.assertEqual(approve_status, 200)
        self.assertEqual(approved["changeRequest"]["status"], "approved")
        employee = self.store.get_employee(employee_id)
        self.assertEqual(employee["title"], "Needs Approval")
        self.assertEqual(employee["manager_approved"], 1)
        self.assertEqual(employee["access_profile"]["software_access"], "VPN")

        reject_status, rejected = self.request(
            "POST",
            f"/api/change-requests/{viewer_request_payload['changeRequest']['id']}/reject",
            {"note": "Not needed"},
            headers=self.session_headers(name="Approving Admin", email="approver@gcefcu.org"),
        )
        self.assertEqual(reject_status, 200)
        self.assertEqual(rejected["changeRequest"]["status"], "rejected")
        self.assertEqual(self.store.get_employee(employee_id)["department"], "")

        _, audit = self.request("GET", "/api/audit-log")
        actions = [entry["action"] for entry in audit["audit"]]
        self.assertIn("request_change", actions)
        self.assertIn("approve_change_request", actions)
        self.assertIn("reject_change_request", actions)

    def test_trusted_proxy_auth_uses_ad_group_headers_for_admin_actions(self):
        env = {
            "GATEWATCH_AUTH_MODE": "trusted_proxy",
            "GATEWATCH_PROXY_SECRET": "proxy-secret-value",
            "ACCESS_REGISTER_PROXY_SECRET": "",
            "GATEWATCH_ADMIN_GROUP_CANONICAL": "gcefcu.org/Users/Domain Admins",
        }
        admin_headers = self.trusted_proxy_headers(
            name="Proxy Admin",
            email="proxy.admin@gcefcu.org",
            groups="Employee Access, Domain Admins",
        )
        viewer_headers = self.trusted_proxy_headers(
            name="Proxy Viewer",
            email="proxy.viewer@gcefcu.org",
            groups="Employee Access",
        )

        with mock.patch.dict(os.environ, env):
            _, auth = self.request("GET", "/api/auth/status", headers=admin_headers)
            self.assertEqual(auth["entra"]["provider"], "trusted_proxy")
            self.assertEqual(auth["entra"]["user"]["actor"], "Proxy Admin (proxy.admin@gcefcu.org)")
            self.assertTrue(auth["entra"]["permissions"]["canModifyEmployees"])

            _, created = self.request(
                "POST",
                "/api/employees",
                {
                    "employee_id": "E-PROXY",
                    "name": "Proxy User",
                    "email": "proxy.user@example.com",
                },
                headers=admin_headers,
            )
            employee_id = created["employee"]["id"]

            update_status, updated = self.request(
                "PATCH",
                f"/api/employees/{employee_id}",
                {"department": "IT"},
                headers=admin_headers,
            )
            viewer_status, viewer_request = self.request(
                "PATCH",
                f"/api/employees/{employee_id}",
                {"title": "Needs proxy approval"},
                headers=viewer_headers,
            )
            delete_status, delete_error = self.request(
                "DELETE",
                f"/api/employees/{employee_id}",
                expected_error=403,
                headers=viewer_headers,
            )
            bad_status, bad_error = self.request(
                "GET",
                "/api/auth/status",
                expected_error=403,
                headers={**admin_headers, "X-Gatewatch-Proxy-Secret": "wrong-secret"},
            )

        self.assertEqual(update_status, 200)
        self.assertEqual(updated["employee"]["department"], "IT")
        self.assertEqual(viewer_status, 202)
        self.assertEqual(viewer_request["changeRequest"]["requested_by"], "Proxy Viewer (proxy.viewer@gcefcu.org)")
        self.assertEqual(delete_status, 403)
        self.assertIn("Domain Admins", delete_error["error"])
        self.assertEqual(bad_status, 403)
        self.assertIn("proxy secret", bad_error["error"])

    def test_admin_config_requires_domain_admin_and_masks_secrets(self):
        viewer_headers = self.session_headers(can_modify=False, name="Viewer User", email="viewer@gcefcu.org")
        config_file = Path(self.tempdir.name) / "gatewatch.env"
        secrets_env = {
            "GATEWATCH_CONFIG_FILE": str(config_file),
            "GATEWATCH_SESSION_SECRET": "server-session-secret",
            "GATEWATCH_ENTRA_TENANT_ID": "example-tenant",
            "GATEWATCH_ENTRA_CLIENT_ID": "example-client",
            "GATEWATCH_ENTRA_CLIENT_SECRET": "server-client-secret",
            "GATEWATCH_ENTRA_REDIRECT_URI": f"{self.base_url}/auth/entra/callback",
            "GATEWATCH_ADMIN_GROUP_CANONICAL": "gcefcu.org/Users/Domain Admins",
        }

        with mock.patch.dict(os.environ, secrets_env):
            unauth_status, unauth_error = self.request("GET", "/api/admin/config", expected_error=403)
            viewer_status, viewer_error = self.request(
                "GET",
                "/api/admin/config",
                expected_error=403,
                headers=viewer_headers,
            )
            viewer_save_status, viewer_save_error = self.request(
                "POST",
                "/api/admin/config",
                {
                    "host": "127.0.0.1",
                    "port": "8087",
                    "databasePath": str(Path(self.tempdir.name) / "gatewatch.db"),
                    "adminGroupCanonical": "gcefcu.org/Users/Domain Admins",
                    "tenantId": "viewer-tenant",
                    "clientId": "viewer-client",
                    "redirectUri": f"{self.base_url}/auth/entra/callback",
                    "sessionSecret": "viewer-session-secret",
                    "clientSecret": "viewer-client-secret",
                    "allowInsecureNetwork": False,
                },
                expected_error=403,
                headers=viewer_headers,
            )
            status, payload = self.request("GET", "/api/admin/config", headers=self.session_headers())
            self.assertEqual(unauth_status, 403)
            self.assertEqual(viewer_status, 403)
            self.assertEqual(viewer_save_status, 403)
            self.assertIn("Domain Admins", unauth_error["error"])
            self.assertIn("Domain Admins", viewer_error["error"])
            self.assertIn("Domain Admins", viewer_save_error["error"])
            self.assertEqual(status, 200)
            self.assertEqual(payload["config"]["configFile"]["path"], str(config_file))
            self.assertTrue(payload["config"]["secrets"]["sessionSecret"]["configured"])
            self.assertTrue(payload["config"]["secrets"]["entraClientSecret"]["configured"])
            encoded = json.dumps(payload)
            self.assertNotIn("server-session-secret", encoded)
            self.assertNotIn("server-client-secret", encoded)
            self.assertIn('GATEWATCH_ENTRA_CLIENT_SECRET="<already set on server>"', payload["config"]["envTemplate"])
            self.assertIn(f'GATEWATCH_CONFIG_FILE="{str(config_file).replace(chr(92), chr(92) + chr(92))}"', payload["config"]["envTemplate"])
            self.assertIn('GATEWATCH_ADMIN_GROUP_CANONICAL="gcefcu.org/Users/Domain Admins"', payload["config"]["envTemplate"])

            _, preview = self.request(
                "POST",
                "/api/admin/config/validate",
                {
                    "host": "0.0.0.0",
                    "port": "8087",
                    "databasePath": str(Path(self.tempdir.name) / "gatewatch.db"),
                    "adminGroupCanonical": "gcefcu.org/Users/Domain Admins",
                    "tenantId": "example-tenant",
                    "clientId": "example-client",
                    "redirectUri": f"{self.base_url}/auth/entra/callback",
                    "sessionSecret": "typed-session-secret",
                    "clientSecret": "typed-client-secret",
                    "allowInsecureNetwork": False,
                },
                headers=self.session_headers(),
            )
            preview_text = json.dumps(preview)
            self.assertNotIn("typed-session-secret", preview_text)
            self.assertNotIn("typed-client-secret", preview_text)
            self.assertIn('GATEWATCH_SESSION_SECRET="<provided in form>"', preview["preview"]["envTemplate"])
            network_check = next(check for check in preview["preview"]["checks"] if check["key"] == "network")
            self.assertTrue(network_check["blocked"])

            save_status, saved = self.request(
                "POST",
                "/api/admin/config",
                {
                    "host": "127.0.0.1",
                    "port": "8087",
                    "databasePath": str(Path(self.tempdir.name) / "gatewatch.db"),
                    "adminGroupCanonical": "gcefcu.org/Users/Domain Admins",
                    "tenantId": "saved-tenant",
                    "clientId": "saved-client",
                    "redirectUri": f"{self.base_url}/auth/entra/callback",
                    "sessionSecret": "typed-session-secret",
                    "clientSecret": "typed-client-secret",
                    "allowInsecureNetwork": False,
                },
                headers=self.session_headers(),
            )
            saved_text = json.dumps(saved)
            self.assertEqual(save_status, 200)
            self.assertTrue(saved["config"]["saveStatus"]["saved"])
            self.assertTrue(saved["config"]["saveStatus"]["verified"])
            self.assertTrue(saved["config"]["configFile"]["exists"])
            self.assertEqual(saved["config"]["runtime"]["tenantId"], "saved-tenant")
            self.assertEqual(saved["config"]["runtime"]["clientId"], "saved-client")
            self.assertTrue(saved["config"]["secrets"]["sessionSecret"]["configured"])
            self.assertTrue(saved["config"]["secrets"]["entraClientSecret"]["configured"])
            self.assertNotIn("typed-session-secret", saved_text)
            self.assertNotIn("typed-client-secret", saved_text)
            env_file_text = config_file.read_text(encoding="utf-8")
            self.assertIn('GATEWATCH_ENTRA_TENANT_ID="saved-tenant"', env_file_text)
            self.assertIn('GATEWATCH_ENTRA_CLIENT_ID="saved-client"', env_file_text)
            self.assertIn('GATEWATCH_ENTRA_CLIENT_SECRET="typed-client-secret"', env_file_text)
            self.assertIn('GATEWATCH_SESSION_SECRET="typed-session-secret"', env_file_text)

            _, auth = self.request("GET", "/api/auth/status")
            self.assertTrue(auth["entra"]["configured"])
            self.assertTrue(auth["entra"]["ssoConfigured"])
            self.assertTrue(auth["entra"]["graphConfigured"])

    def test_admin_diagnostics_requires_domain_admin_and_redacts_secrets(self):
        viewer_headers = self.session_headers(can_modify=False, name="Viewer User", email="viewer@gcefcu.org")
        _, created = self.request(
            "POST",
            "/api/employees",
            {
                "employee_id": "E-DIAG",
                "name": "Diagnostic User",
                "email": "diagnostic.user@example.com",
            },
        )
        self.request(
            "PATCH",
            f"/api/employees/{created['employee']['id']}",
            {"title": "Pending Diagnostic Review"},
            headers=viewer_headers,
        )
        secrets_env = {
            "GATEWATCH_SESSION_SECRET": "diagnostic-session-secret",
            "GATEWATCH_ENTRA_TENANT_ID": "diagnostic-tenant",
            "GATEWATCH_ENTRA_CLIENT_ID": "diagnostic-client",
            "GATEWATCH_ENTRA_CLIENT_SECRET": "diagnostic-client-secret",
            "GATEWATCH_ENTRA_REDIRECT_URI": f"{self.base_url}/auth/entra/callback",
            "GATEWATCH_ADMIN_GROUP_CANONICAL": "gcefcu.org/Users/Domain Admins",
            "GATEWATCH_DB": str(Path(self.tempdir.name) / "http.db"),
        }

        with mock.patch.dict(os.environ, secrets_env):
            unauth_status, unauth_error = self.request("GET", "/api/admin/diagnostics", expected_error=403)
            viewer_status, viewer_error = self.request(
                "GET",
                "/api/admin/diagnostics",
                expected_error=403,
                headers=viewer_headers,
            )
            status, payload = self.request("GET", "/api/admin/diagnostics", headers=self.session_headers())

        self.assertEqual(unauth_status, 403)
        self.assertEqual(viewer_status, 403)
        self.assertIn("Domain Admins", unauth_error["error"])
        self.assertIn("Domain Admins", viewer_error["error"])
        self.assertEqual(status, 200)
        diagnostics = payload["diagnostics"]
        self.assertEqual(diagnostics["health"]["status"], "ok")
        self.assertEqual(diagnostics["database"]["quickCheck"], "ok")
        self.assertGreaterEqual(diagnostics["database"]["rowCounts"]["employees"], 1)
        self.assertGreaterEqual(diagnostics["database"]["rowCounts"]["audit_log"], 1)
        self.assertTrue(diagnostics["auth"]["ssoConfigured"])
        self.assertTrue(diagnostics["auth"]["graphConfigured"])
        self.assertEqual(diagnostics["auth"]["adminGroup"], "gcefcu.org/Users/Domain Admins")
        self.assertTrue(diagnostics["storage"]["exists"])
        self.assertIn("create", [entry["action"] for entry in diagnostics["recentAudit"]])
        self.assertEqual(diagnostics["recentChangeRequests"][0]["status"], "pending")
        encoded = json.dumps(diagnostics)
        self.assertNotIn("diagnostic-session-secret", encoded)
        self.assertNotIn("diagnostic-client-secret", encoded)

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

            status, payload = self.request("POST", "/api/entra/sync", headers=self.session_headers())
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
