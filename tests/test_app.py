import csv
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys


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
        self.assertIn("active or terminated", bad_status.exception.message)

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

    def request(self, method, path, body=None, expected_error=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
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


if __name__ == "__main__":
    unittest.main()
