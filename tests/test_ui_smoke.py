import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MAX_JSON_BODY_BYTES, STATIC_DIR, Store, make_handler  # noqa: E402


class SmokeTestServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.ready = threading.Event()
        self.server_error = None
        super().__init__(*args, **kwargs)

    def serve_forever(self, poll_interval=0.05):
        self.ready.set()
        try:
            super().serve_forever(poll_interval=poll_interval)
        except Exception as error:
            self.server_error = error
            raise


class SmokeStoreProxy:
    def __init__(self):
        self.current = None

    def use(self, store):
        self.current = store

    def __getattr__(self, name):
        if self.current is None:
            raise RuntimeError("UI smoke store is not initialized")
        return getattr(self.current, name)


class AccessRegisterUiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store_proxy = SmokeStoreProxy()
        base_handler = make_handler(cls.store_proxy, STATIC_DIR)

        class QuietHandler(base_handler):
            def log_message(self, _format, *args):
                return

        handler = QuietHandler
        cls.server = SmokeTestServer(("127.0.0.1", 0), handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.wait_for_server()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "ui-smoke.db"
        self.store = Store(self.db_path)
        self.store.init(seed=True)
        self.store_proxy.use(self.store)

    @classmethod
    def stop_server(cls):
        if cls.thread.is_alive():
            cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    @classmethod
    def wait_for_server(cls):
        if not cls.server.ready.wait(timeout=5):
            if cls.server.server_error:
                raise AssertionError("UI smoke server failed before readiness") from cls.server.server_error
            raise AssertionError("UI smoke server thread did not enter serve_forever")

        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            if cls.server.server_error:
                raise AssertionError("UI smoke server stopped during readiness") from cls.server.server_error
            if not cls.thread.is_alive():
                raise AssertionError(f"UI smoke server thread stopped before readiness: {last_error}")
            try:
                with urllib.request.urlopen(f"{cls.base_url}/", timeout=1) as response:
                    if response.status != 200:
                        last_error = AssertionError(f"Unexpected readiness status {response.status}")
                        time.sleep(0.05)
                        continue
                    return
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.05)
        raise AssertionError(f"UI smoke server did not start: {last_error}")

    def request(self, method, path, body=None, role="Admin", actor="UI Smoke", expected_error=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-App-Role": role,
                "X-App-Actor": actor,
            },
        )
        last_error = None
        for _attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    raw = response.read().decode("utf-8")
                    if expected_error:
                        raise AssertionError(f"{method} {path} succeeded, expected HTTP {expected_error}")
                    if response.headers.get_content_type() == "application/json":
                        return json.loads(raw)
                    return raw
            except urllib.error.HTTPError as error:
                details = error.read().decode("utf-8")
                if expected_error and error.code == expected_error:
                    return json.loads(details) if details else {}
                raise AssertionError(f"{method} {path} failed with {error.code}: {details}") from error
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.1)
        raise AssertionError(f"{method} {path} failed after retries: {last_error}")

    def request_without_app_headers(self, method, path, body=None, expected_error=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        last_error = None
        for _attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    raw = response.read().decode("utf-8")
                    if expected_error:
                        raise AssertionError(f"{method} {path} succeeded, expected HTTP {expected_error}")
                    return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
            except urllib.error.HTTPError as error:
                details = error.read().decode("utf-8")
                if expected_error and error.code == expected_error:
                    return json.loads(details) if details else {}
                raise AssertionError(f"{method} {path} failed with {error.code}: {details}") from error
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.1)
        raise AssertionError(f"{method} {path} failed after retries: {last_error}")

    def raw_json_request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            connection.putrequest(method, path)
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            connection.endheaders()
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
        finally:
            connection.close()

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body, **kwargs):
        return self.request("POST", path, body, **kwargs)

    def patch(self, path, body, **kwargs):
        return self.request("PATCH", path, body, **kwargs)

    def system_named(self, name):
        systems = self.get("/api/systems")["systems"]
        return next(system for system in systems if system["name"] == name)

    def employee_named(self, name):
        employees = self.get("/api/employees")["employees"]
        return next(employee for employee in employees if employee["name"] == name)

    def employee_identifier(self, employee_id):
        employees = self.get("/api/employees")["employees"]
        return next(employee for employee in employees if employee["employee_id"] == employee_id)

    def workflow_step(self, number, description):
        return self.subTest(workflow_step=f"{number}. {description}")

    def test_static_ui_assets_expose_smoke_controls(self):
        html = self.get("/")
        app_js = self.get("/app.js")

        self.assertIn('id="evidenceDialog"', html)
        self.assertIn('aria-labelledby="evidenceDialogTitle"', html)
        self.assertIn('id="inventorySearchInventory"', html)
        self.assertIn('data-inventory-search', html)
        self.assertIn('id="evidenceForm" class="modal-panel" novalidate', html)
        self.assertIn("function openEvidenceDialog", app_js)
        self.assertIn("function fetchWithTimeout", app_js)
        self.assertIn("function syncInventoryFilters", app_js)
        self.assertIn("function notificationDisabled", app_js)
        self.assertIn("state.filterText = event.target.value.trim();", app_js)
        self.assertIn("const filterText = state.filterText.toLowerCase();", app_js)
        self.assertIn("function setActionDisabled", app_js)
        self.assertIn('setActionDisabled("route-disabled-removals"', app_js)
        self.assertIn('setActionDisabled("run-backup"', app_js)
        self.assertIn('name="product_name"', html)
        self.assertIn('name="application_url"', html)
        self.assertIn('id="resourceCategoryForm"', html)
        self.assertIn('name="resource_category_id"', html)
        self.assertIn("resourceCategories", app_js)
        self.assertIn("function systemLabel", app_js)
        self.assertIn('id="configurationView"', html)
        self.assertIn('id="configurationAuthForm"', html)
        self.assertIn('id="configurationAdScheduleForm"', html)
        self.assertIn('id="configurationConnectorForm"', html)
        self.assertIn('id="configurationBackupForm"', html)
        self.assertIn('data-view="configuration"', html)
        self.assertIn('data-config-tab="identity"', html)
        self.assertIn('data-action="goto-config-tab"', html)
        self.assertIn("function setConfigurationTab", app_js)
        self.assertIn("function renderConfiguration", app_js)
        self.assertIn("function renderConfigurationTabs", app_js)
        self.assertIn("function backupRunHtml", app_js)
        self.assertIn("function connectorHtml", app_js)
        self.assertIn("function importRunHtml", app_js)
        self.assertIn('setFormDisabled("#configurationAuthForm"', app_js)
        self.assertNotIn("window.prompt", app_js)

    def test_http_system_metadata_is_available_to_access_forms(self):
        system = self.post(
            "/api/systems",
            {
                "name": "Shipping Portal",
                "product_name": "Fulfillment Cloud",
                "application_url": "https://shipping.example.local",
                "admin_url": "https://shipping.example.local/admin",
                "documentation_url": "https://docs.example.local/shipping",
                "category": "software",
                "owner": "Logistics Systems",
                "risk_level": "standard",
                "review_frequency_days": 45,
            },
        )["system"]

        systems = self.get("/api/bootstrap")["systems"]
        categories = self.get("/api/bootstrap")["resourceCategories"]
        stored = next(item for item in systems if item["id"] == system["id"])

        self.assertEqual(stored["product_name"], "Fulfillment Cloud")
        self.assertEqual(stored["application_url"], "https://shipping.example.local")
        self.assertEqual(stored["admin_url"], "https://shipping.example.local/admin")
        self.assertEqual(stored["documentation_url"], "https://docs.example.local/shipping")
        self.assertEqual(stored["resource_category_name"], "Business Applications")
        self.assertTrue(any(category["name"] == "Social Media" for category in categories))

    def test_http_role_authorization_matches_documented_hr_scope(self):
        employee = self.post(
            "/api/employees",
            {
                "employee_id": "E-HR-1",
                "name": "HR Created",
                "email": "hr.created@example.local",
                "department": "People",
                "location": "HQ",
            },
            role="HR",
            actor="UI Smoke HR",
        )["employee"]
        vpn = self.system_named("Company VPN")

        request = self.post(
            "/api/access-requests",
            {
                "requester": "UI Smoke HR",
                "employee_id": employee["id"],
                "system_id": vpn["id"],
                "access_type": "user",
                "access_level": "Standard User",
                "business_reason": "New hire setup.",
            },
            role="HR",
            actor="UI Smoke HR",
        )["accessRequest"]
        credential = self.post(
            "/api/physical-credentials",
            {
                "employee_id": employee["id"],
                "credential_type": "badge",
                "location": "HQ",
                "credential_identifier": "Badge-HR-SMOKE",
                "status": "active",
            },
            role="HR",
            actor="UI Smoke HR",
        )["physicalCredential"]
        campaign = self.post(
            "/api/review-campaigns",
            {
                "name": "Reviewer-created access campaign",
                "owner": "IT Security",
                "due_date": "2026-07-31",
                "frequency_days": 90,
            },
            role="Reviewer",
            actor="UI Smoke Reviewer",
        )["reviewCampaign"]
        error = self.post(
            "/api/systems",
            {
                "name": "HR Blocked System",
                "category": "software",
                "owner": "IT Security",
                "risk_level": "standard",
            },
            role="HR",
            actor="UI Smoke HR",
            expected_error=403,
        )

        self.assertEqual(request["status"], "pending")
        self.assertEqual(credential["credential_identifier"], "Badge-HR-SMOKE")
        self.assertEqual(campaign["status"], "open")
        self.assertIn("HR role cannot perform this action", error["error"])

    def test_http_mutations_without_app_role_headers_fail_closed(self):
        error = self.request_without_app_headers(
            "POST",
            "/api/backups/run",
            {"retention_days": 90},
            expected_error=403,
        )

        self.assertIn("ReadOnly role cannot perform this action", error["error"])

    def test_http_rejects_bad_or_oversized_json_bodies(self):
        base_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-App-Role": "Admin",
            "X-App-Actor": "UI Smoke",
        }

        bad_status, bad_error = self.raw_json_request(
            "POST",
            "/api/backups/run",
            {**base_headers, "Content-Length": "not-a-number"},
        )
        large_status, large_error = self.raw_json_request(
            "POST",
            "/api/backups/run",
            {**base_headers, "Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
        )

        self.assertEqual(bad_status, 400)
        self.assertIn("Content-Length must be a valid integer", bad_error["error"])
        self.assertEqual(large_status, 413)
        self.assertIn("Request body must be", large_error["error"])

    def test_unexpected_http_errors_do_not_expose_exception_details(self):
        class FailingStore:
            def summary(self):
                raise RuntimeError("sensitive database path C:\\secret\\access_register.db")

        self.store_proxy.use(FailingStore())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            error = self.get("/api/summary", expected_error=500)

        self.assertEqual(error["error"], "Internal server error")
        self.assertNotIn("sensitive database path", json.dumps(error))
        self.assertIn("Unhandled RuntimeError while handling GET /api/summary", stderr.getvalue())
        self.assertNotIn("sensitive database path", stderr.getvalue())

    def test_readonly_bootstrap_does_not_expose_scheduled_ad_payload(self):
        secret_payload = "\n".join(
            [
                "employee_id,email,name,enabled",
                "E-SECRET,secret.person@example.local,Secret Person,false",
            ]
        )
        self.post(
            "/api/ad-sync-settings",
            {
                "enabled": True,
                "format": "csv",
                "directory_text": secret_payload,
                "interval_hours": 24,
            },
        )

        bootstrap = self.get("/api/bootstrap", role="ReadOnly", actor="ReadOnly User")
        settings_error = self.get("/api/ad-sync-settings", role="ReadOnly", actor="ReadOnly User", expected_error=403)
        admin_settings = self.get("/api/bootstrap")["adSyncSettings"]

        self.assertNotIn("secret.person@example.local", json.dumps(bootstrap))
        self.assertIn("ReadOnly role cannot perform this action", settings_error["error"])
        self.assertIsNone(bootstrap["adSyncSettings"]["directory_text"])
        self.assertTrue(bootstrap["adSyncSettings"]["has_directory_payload"])
        self.assertIn("secret.person@example.local", admin_settings["directory_text"])

    def test_readonly_backup_payloads_hide_filesystem_paths(self):
        admin_backup = self.post("/api/backups/run", {"retention_days": 90})["backup"]

        bootstrap = self.get("/api/bootstrap", role="ReadOnly", actor="ReadOnly User")
        backups = self.get("/api/backups", role="ReadOnly", actor="ReadOnly User")["backups"]

        self.assertTrue(Path(admin_backup["backup_path"]).exists())
        self.assertIsNone(bootstrap["backups"][0]["backup_path"])
        self.assertFalse(bootstrap["backups"][0]["path_visible"])
        self.assertIsNone(backups[0]["backup_path"])
        self.assertFalse(backups[0]["path_visible"])
        self.assertNotIn(str(self.db_path), json.dumps(bootstrap))

    def test_full_manual_ui_workflow_outcomes_over_http(self):
        with self.workflow_step(1, "Dashboard data is available for the first rendered view"):
            summary = self.get("/api/summary")
            self.assertEqual(summary["staleReviews"], 1)
            self.assertGreaterEqual(summary["activeAccess"], 4)
            self.assertEqual(len(self.get("/api/access-records")["accessRecords"]), 5)

        with self.workflow_step(2, "Inspect an employee from the access inventory table"):
            first_record = self.get("/api/access-records")["accessRecords"][0]
            detail = self.get(f"/api/employees/{first_record['employee_id']}")
            self.assertIn("employee", detail)
            self.assertGreaterEqual(len(detail["access"]), 1)

        with self.workflow_step(3, "Certify a stale record from Reviews"):
            stale_record = next(record for record in self.get("/api/access-records")["accessRecords"] if record["is_stale"])
            self.post(
                f"/api/access-records/{stale_record['id']}/review",
                {"decision": "certified", "notes": "Certified by UI smoke test."},
            )
            review_records = [
                record
                for record in self.get("/api/access-records")["accessRecords"]
                if record["is_stale"] or record["status"] == "unknown"
            ]
            self.assertEqual(review_records, [])

        with self.workflow_step(4, "Complete a terminated employee removal with evidence"):
            removal = next(
                record
                for record in self.get("/api/access-records")["accessRecords"]
                if record["status"] == "removal_pending"
            )
            updated = self.patch(
                f"/api/access-records/{removal['id']}",
                {"status": "removed", "removal_evidence": "UI smoke evidence ticket."},
            )["accessRecord"]
            self.assertEqual(updated["status"], "removed")
            self.assertEqual(updated["removal_evidence"], "UI smoke evidence ticket.")

        with self.workflow_step(5, "Import sample CSV and confirm unmatched accounts increase"):
            vpn = self.system_named("Company VPN")
            before_unmatched = self.get("/api/summary")["unmatchedImports"]
            import_result = self.post(
                "/api/imports/accounts",
                {
                    "system_id": vpn["id"],
                    "source_name": "UI smoke account export",
                    "csv_text": "\n".join(
                        [
                            "employee_id,email,name,account,role,access_type",
                            "E-1001,avery.morgan@example.local,Avery Morgan,avery.admin,Administrator,admin",
                            "E-1003,priya.shah@example.local,Priya Shah,pshah.user,Standard User,user",
                            ",unknown.contractor@example.local,Unknown Contractor,contractor.ext,Administrator,admin",
                        ]
                    ),
                },
            )["importRun"]
            self.assertEqual(import_result["unmatched_rows"], 1)
            self.assertGreater(self.get("/api/summary")["unmatchedImports"], before_unmatched)

        with self.workflow_step(6, "Sync sample AD CSV and confirm new users plus disabled flags"):
            ad_result = self.post(
                "/api/ad/sync",
                {
                    "source_name": "UI smoke AD export",
                    "format": "csv",
                    "directory_text": "\n".join(
                        [
                            "EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName",
                            "E-1001,Avery Morgan,avery.morgan@example.local,Operations,HQ,Dana Chen,TRUE,guid-1001,avery.morgan@example.local,amorgan",
                            "E-1005,Taylor Kim,taylor.kim@example.local,Operations,HQ,Dana Chen,TRUE,guid-1005,taylor.kim@example.local,tkim",
                            "E-1006,Rene Carter,rene.carter@example.local,Finance,HQ,Riley Brooks,FALSE,guid-1006,rene.carter@example.local,rcarter",
                        ]
                    ),
                },
            )["adSyncRun"]
            self.assertEqual(ad_result["created_users"], 2)
            self.assertEqual(ad_result["disabled_users"], 1)
            self.assertEqual(self.employee_identifier("E-1006")["ad_enabled"], 0)

        with self.workflow_step(7, "Manual override preserves local identity fields while AD enabled state updates"):
            avery = self.employee_identifier("E-1001")
            self.patch(
                f"/api/employees/{avery['id']}",
                {
                    "name": "Avery Local",
                    "email": "avery.local@example.local",
                    "department": "Special Projects",
                    "location": "Field Office",
                    "manager": "Local Manager",
                    "admin_override": True,
                },
            )
            self.post(
                "/api/ad/sync",
                {
                    "source_name": "UI smoke override AD export",
                    "format": "csv",
                    "directory_text": "\n".join(
                        [
                            "EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName",
                            "E-1001,Directory Avery,directory.avery@example.local,Accounting,HQ,Directory Manager,FALSE,guid-avery-disabled,directory.avery@example.local,davery",
                        ]
                    ),
                },
            )
            avery_detail = self.get(f"/api/employees/{avery['id']}")["employee"]
            self.assertEqual(avery_detail["name"], "Avery Local")
            self.assertEqual(avery_detail["department"], "Special Projects")
            self.assertEqual(avery_detail["ad_enabled"], 0)
            self.assertEqual(avery_detail["ad_sam_account_name"], "davery")

        with self.workflow_step(8, "Approve a request and confirm the access record keeps expiration"):
            request = self.post(
                "/api/access-requests",
                {
                    "requester": "UI Smoke",
                    "employee_id": avery["id"],
                    "system_id": vpn["id"],
                    "access_type": "admin",
                    "access_level": "Temporary Admin",
                    "expiration_date": "2026-07-15",
                    "business_reason": "Temporary access for smoke workflow.",
                },
            )["accessRequest"]
            decided = self.post(
                f"/api/access-requests/{request['id']}/decision",
                {"decision": "approve", "decision_notes": "Approved by UI smoke."},
            )["accessRequest"]
            created_record = next(
                record
                for record in self.get("/api/access-records")["accessRecords"]
                if record["id"] == decided["created_access_record_id"]
            )
            self.assertEqual(created_record["expires_at"], "2026-07-15")

        with self.workflow_step(9, "Route disabled-user access and confirm it moves to removal pending"):
            routed = self.post("/api/disabled-access/route-removal", {})["result"]
            self.assertGreaterEqual(routed["routed"], 1)
            queue_statuses = {record["status"] for record in self.get("/api/disabled-access")["disabledAccess"]}
            self.assertEqual(queue_statuses, {"removal_pending"})

        with self.workflow_step(10, "Create a review campaign and mark it complete"):
            campaign = self.post(
                "/api/review-campaigns",
                {
                    "name": "UI smoke quarterly access review",
                    "owner": "IT Security",
                    "due_date": "2026-07-31",
                    "frequency_days": 90,
                },
            )["reviewCampaign"]
            completed = self.patch(
                f"/api/review-campaigns/{campaign['id']}",
                {"status": "complete", "notes": "Completed by UI smoke."},
            )["reviewCampaign"]
            self.assertEqual(completed["status"], "complete")

        with self.workflow_step(11, "Add shared account and physical credential"):
            shared = self.post(
                "/api/shared-accounts",
                {
                    "system_id": vpn["id"],
                    "account_name": "ui-smoke-breakglass",
                    "owner": "IT Security",
                    "rotation_due_at": "2026-08-15",
                    "mfa_enabled": True,
                    "approved_users": "Avery Local, IT Security",
                    "business_reason": "Emergency recovery.",
                },
            )["sharedAccount"]
            credential = self.post(
                "/api/physical-credentials",
                {
                    "employee_id": avery["id"],
                    "credential_type": "badge",
                    "location": "HQ",
                    "credential_identifier": "Badge-UI-SMOKE",
                    "zone": "Operations",
                    "status": "active",
                    "due_at": "2026-08-20",
                    "evidence": "Issued by UI smoke.",
                },
            )["physicalCredential"]
            self.assertEqual(shared["account_name"], "ui-smoke-breakglass")
            self.assertEqual(credential["credential_identifier"], "Badge-UI-SMOKE")

        with self.workflow_step(12, "Add connector plan and update auth settings"):
            connector = self.post(
                "/api/connectors",
                {
                    "name": "Microsoft 365 UI Smoke",
                    "connector_type": "microsoft_365",
                    "owner": "IT Security",
                    "status": "planned",
                    "instructions": "Graph export scope and credential owner pending.",
                },
            )["connector"]
            auth_settings = self.post(
                "/api/auth-settings",
                {
                    "provider": "active_directory",
                    "login_required": True,
                    "admin_group": "DOMAIN\\AccessRegister-Admins",
                    "reviewer_group": "DOMAIN\\AccessRegister-Reviewers",
                    "hr_group": "DOMAIN\\AccessRegister-HR",
                    "readonly_group": "DOMAIN\\AccessRegister-ReadOnly",
                    "notes": "UI smoke auth settings.",
                },
            )["authSettings"]
            self.assertEqual(connector["status"], "planned")
            self.assertEqual(auth_settings["provider"], "active_directory")
            self.assertEqual(auth_settings["login_required"], 1)

        with self.workflow_step(13, "Run backup and confirm a backup run appears"):
            backup = self.post("/api/backups/run", {"retention_days": 90})["backup"]
            self.assertEqual(backup["status"], "complete")
            self.assertTrue(Path(backup["backup_path"]).exists())

        with self.workflow_step(14, "Check Audit Log for recorded actions"):
            audit = self.get("/api/audit-log")["audit"]
            summaries = "\n".join(entry["summary"] for entry in audit)
            self.assertGreaterEqual(len(audit), 15)
            for expected in [
                "Backup complete",
                "Updated authentication settings",
                "Created connector plan",
                "Created physical credential",
                "Created shared account",
                "Routed",
                "Approved access request",
                "Synced",
                "Imported",
                "Updated access record",
            ]:
                self.assertIn(expected, summaries)


class TrustedProxyAuthSmokeTests(unittest.TestCase):
    proxy_secret = "trusted-proxy-smoke-secret"

    @classmethod
    def setUpClass(cls):
        cls.previous_proxy_secret = os.environ.get("ACCESS_REGISTER_PROXY_SECRET")
        os.environ["ACCESS_REGISTER_PROXY_SECRET"] = cls.proxy_secret
        cls.store_proxy = SmokeStoreProxy()
        base_handler = make_handler(cls.store_proxy, STATIC_DIR, auth_mode="trusted_proxy")

        class QuietHandler(base_handler):
            def log_message(self, _format, *args):
                return

        cls.server = SmokeTestServer(("127.0.0.1", 0), QuietHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.wait_for_server(expect_unauthenticated=True)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "trusted-proxy.db"
        self.store = Store(self.db_path)
        self.store.init(seed=True)
        self.store.update_auth_settings(
            {
                "provider": "active_directory",
                "login_required": True,
                "admin_group": "DOMAIN\\AccessRegister-Admins",
                "supervisor_group": "DOMAIN\\AccessRegister-Supervisors",
                "hr_group": "DOMAIN\\AccessRegister-HR",
                "readonly_group": "DOMAIN\\AccessRegister-ReadOnly",
            },
            actor="Setup",
            role="Admin",
        )
        self.store_proxy.use(self.store)

    @classmethod
    def stop_server(cls):
        if cls.thread.is_alive():
            cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        if cls.previous_proxy_secret is None:
            os.environ.pop("ACCESS_REGISTER_PROXY_SECRET", None)
        else:
            os.environ["ACCESS_REGISTER_PROXY_SECRET"] = cls.previous_proxy_secret

    @classmethod
    def wait_for_server(cls, expect_unauthenticated=False):
        if not cls.server.ready.wait(timeout=5):
            raise AssertionError("Trusted proxy smoke server thread did not enter serve_forever")

        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{cls.base_url}/", timeout=1) as response:
                    if expect_unauthenticated:
                        last_error = AssertionError(f"Unexpected authenticated readiness status {response.status}")
                        time.sleep(0.05)
                        continue
                    return
            except urllib.error.HTTPError as error:
                if expect_unauthenticated and error.code == 403:
                    return
                last_error = error
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
            time.sleep(0.05)
        raise AssertionError(f"Trusted proxy smoke server did not start: {last_error}")

    def headers(self, user, email=None, groups=None, extra=None):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Access-Register-Proxy-Secret": self.proxy_secret,
            "X-Remote-User": user,
            "X-Remote-Email": email or user,
            "X-Remote-Name": user.split("@", 1)[0],
        }
        if groups:
            headers["X-Remote-Groups"] = groups
        if extra:
            headers.update(extra)
        return headers

    def request(self, method, path, body=None, headers=None, expected_error=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                raw = response.read().decode("utf-8")
                if expected_error:
                    raise AssertionError(f"{method} {path} succeeded, expected HTTP {expected_error}")
                return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8")
            if expected_error and error.code == expected_error:
                return json.loads(details) if details else {}
            raise AssertionError(f"{method} {path} failed with {error.code}: {details}") from error

    def get(self, path, headers, **kwargs):
        return self.request("GET", path, headers=headers, **kwargs)

    def post(self, path, body, headers, **kwargs):
        return self.request("POST", path, body, headers=headers, **kwargs)

    def test_trusted_proxy_requires_authenticated_static_request(self):
        secret_error = self.request("GET", "/", expected_error=403)
        user_error = self.request(
            "GET",
            "/",
            headers={"X-Access-Register-Proxy-Secret": self.proxy_secret},
            expected_error=401,
        )

        self.assertIn("Trusted proxy secret is missing or invalid", secret_error["error"])
        self.assertIn("Authenticated proxy user header is required", user_error["error"])

    def test_trusted_proxy_mutations_require_application_header(self):
        headers = self.headers("supervisor@example.local", groups="DOMAIN\\AccessRegister-Supervisors")
        headers.pop("X-Requested-With")

        error = self.post(
            "/api/systems",
            {
                "name": "Blocked CSRF System",
                "category": "software",
                "owner": "IT Security",
                "risk_level": "standard",
            },
            headers=headers,
            expected_error=403,
        )

        self.assertIn("application request header", error["error"])

    def test_employee_role_is_self_service_scoped_and_ignores_spoofed_role_header(self):
        employee = next(item for item in self.store.list_employees() if item["email"] == "avery.morgan@example.local")
        other_employee = next(item for item in self.store.list_employees() if item["id"] != employee["id"])
        headers = self.headers(
            "avery.morgan@example.local",
            extra={"X-App-Role": "Admin", "X-App-Actor": "Spoofed Admin"},
        )

        bootstrap = self.get("/api/bootstrap", headers=headers)
        other_detail_error = self.get(f"/api/employees/{other_employee['id']}", headers=headers, expected_error=403)
        backup_error = self.post("/api/backups/run", {"retention_days": 90}, headers=headers, expected_error=403)
        request = self.post(
            "/api/access-requests",
            {
                "requester": "Avery Morgan",
                "employee_id": employee["id"],
                "system_id": self.store.list_systems()[0]["id"],
                "access_type": "user",
                "access_level": "Standard User",
                "business_reason": "Self-service access request.",
            },
            headers=headers,
        )["accessRequest"]
        other_request_error = self.post(
            "/api/access-requests",
            {
                "requester": "Avery Morgan",
                "employee_id": other_employee["id"],
                "system_id": self.store.list_systems()[0]["id"],
                "access_type": "user",
                "access_level": "Standard User",
                "business_reason": "Should not be able to request for someone else.",
            },
            headers=headers,
            expected_error=403,
        )

        self.assertEqual(bootstrap["session"]["role"], "Employee")
        self.assertTrue(bootstrap["session"]["linkedEmployee"])
        self.assertEqual([item["id"] for item in bootstrap["employees"]], [employee["id"]])
        self.assertTrue(all(record["employee_id"] == employee["id"] for record in bootstrap["accessRecords"]))
        self.assertEqual(bootstrap["audit"], [])
        self.assertEqual(request["employee_id"], employee["id"])
        self.assertIn("only read its own employee record", other_detail_error["error"])
        self.assertIn("Employee role cannot perform this action", backup_error["error"])
        self.assertIn("only submit requests for its own employee record", other_request_error["error"])

    def test_supervisor_group_can_create_resource_and_approve_access_but_not_run_backup(self):
        headers = self.headers(
            "supervisor@example.local",
            groups="DOMAIN\\AccessRegister-Supervisors",
        )
        employee = self.store.list_employees()[0]

        category = self.post(
            "/api/resource-categories",
            {
                "name": "Social Campaigns",
                "description": "Company social media pages and publishing tools.",
                "default_risk_level": "privileged",
            },
            headers=headers,
        )["resourceCategory"]
        system = self.post(
            "/api/systems",
            {
                "name": "Company Facebook",
                "product_name": "Meta Business Suite",
                "application_url": "https://business.facebook.com",
                "resource_category_id": category["id"],
                "category": "software",
                "owner": "Marketing",
                "risk_level": "privileged",
                "review_frequency_days": 90,
                "description": "Company Facebook page administration.",
            },
            headers=headers,
        )["system"]
        access_request = self.post(
            "/api/access-requests",
            {
                "requester": "Supervisor",
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_type": "user",
                "access_level": "Content Publisher",
                "business_reason": "Social campaign support.",
            },
            headers=headers,
        )["accessRequest"]
        decided = self.post(
            f"/api/access-requests/{access_request['id']}/decision",
            {"decision": "approve", "decision_notes": "Approved by supervisor."},
            headers=headers,
        )["accessRequest"]
        backup_error = self.post("/api/backups/run", {"retention_days": 90}, headers=headers, expected_error=403)

        self.assertEqual(category["name"], "Social Campaigns")
        self.assertEqual(system["name"], "Company Facebook")
        self.assertEqual(system["resource_category_name"], "Social Campaigns")
        self.assertEqual(decided["status"], "fulfilled")
        self.assertIsNotNone(decided["created_access_record_id"])
        self.assertIn("Supervisor role cannot perform this action", backup_error["error"])


if __name__ == "__main__":
    unittest.main()
