import contextlib
import csv
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ApiError, ROLE_PERMISSIONS, Store, validate_startup_security  # noqa: E402


class AccessRegisterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"
        self.store = Store(self.db_path)
        self.store.init(seed=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_seed_summary_surfaces_inventory_risks(self):
        summary = self.store.summary()

        self.assertGreaterEqual(summary["activeAccess"], 4)
        self.assertGreaterEqual(summary["privilegedAccess"], 4)
        self.assertGreaterEqual(summary["staleReviews"], 1)
        self.assertGreaterEqual(summary["removalsPending"], 1)
        self.assertGreaterEqual(summary["unmatchedImports"], 1)

    def test_role_permission_contract_matches_ui_controls(self):
        self.assertEqual(ROLE_PERMISSIONS["Admin"], {"create", "update", "review", "import"})
        self.assertEqual(ROLE_PERMISSIONS["Supervisor"], {"create", "update", "review"})
        self.assertEqual(ROLE_PERMISSIONS["Reviewer"], {"review"})
        self.assertEqual(ROLE_PERMISSIONS["HR"], {"create", "update"})
        self.assertEqual(ROLE_PERMISSIONS["Employee"], {"create"})
        self.assertEqual(ROLE_PERMISSIONS["ReadOnly"], set())

    def test_local_auth_cannot_bind_non_loopback_without_explicit_override(self):
        previous = os.environ.pop("ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK", None)
        previous_proxy_secret = os.environ.pop("ACCESS_REGISTER_PROXY_SECRET", None)

        def restore_env():
            if previous is None:
                os.environ.pop("ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK", None)
            else:
                os.environ["ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK"] = previous
            if previous_proxy_secret is None:
                os.environ.pop("ACCESS_REGISTER_PROXY_SECRET", None)
            else:
                os.environ["ACCESS_REGISTER_PROXY_SECRET"] = previous_proxy_secret

        self.addCleanup(restore_env)

        validate_startup_security("127.0.0.1", "local")
        validate_startup_security("localhost", "local")
        with self.assertRaises(RuntimeError) as proxy_context:
            validate_startup_security("0.0.0.0", "trusted_proxy")
        self.assertIn("ACCESS_REGISTER_PROXY_SECRET is required", str(proxy_context.exception))
        os.environ["ACCESS_REGISTER_PROXY_SECRET"] = "test-secret"
        validate_startup_security("0.0.0.0", "trusted_proxy")

        with self.assertRaises(RuntimeError) as context:
            validate_startup_security("0.0.0.0", "local")

        self.assertIn("Refusing to expose local role-selector auth", str(context.exception))

        with self.assertRaises(RuntimeError):
            validate_startup_security("", "local")

        os.environ["ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK"] = "1"
        validate_startup_security("0.0.0.0", "local")

    def test_ad_identity_matches_employee_for_self_service(self):
        employee = self.store.list_employees()[0]

        matched = self.store.find_employee_for_identity(
            {
                "subject": employee["email"],
                "email": employee["email"].upper(),
                "upn": employee["email"],
                "sam": "",
            }
        )
        summary = self.store.employee_summary(employee["id"])

        self.assertEqual(matched["id"], employee["id"])
        self.assertEqual(summary["employees"], 1)
        self.assertGreaterEqual(summary["activeAccess"], 0)

    def test_custom_system_metadata_is_available_for_access_records(self):
        employee = self.store.list_employees()[0]
        social = self.store.create_resource_category(
            {
                "name": "Social Publishing",
                "description": "Company social publishing resources.",
                "default_risk_level": "privileged",
            },
            actor="Test Supervisor",
            role="Supervisor",
        )
        system = self.store.create_system(
            {
                "name": "Shipping Portal",
                "product_name": "Fulfillment Cloud",
                "application_url": "https://shipping.example.local",
                "admin_url": "https://shipping.example.local/admin",
                "documentation_url": "https://docs.example.local/shipping",
                "resource_category_id": social["id"],
                "category": "software",
                "owner": "Logistics Systems",
                "risk_level": "standard",
                "review_frequency_days": 45,
                "description": "Shipping label and fulfillment access.",
            },
            actor="Test Admin",
            role="Admin",
        )

        self.assertEqual(system["product_name"], "Fulfillment Cloud")
        self.assertEqual(system["application_url"], "https://shipping.example.local")
        self.assertEqual(system["admin_url"], "https://shipping.example.local/admin")
        self.assertEqual(system["documentation_url"], "https://docs.example.local/shipping")
        self.assertEqual(system["resource_category_name"], "Social Publishing")

        record = self.store.create_access_record(
            {
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_level": "Standard User",
                "access_type": "user",
                "status": "active",
                "business_reason": "Needs shipping access.",
                "owner": system["owner"],
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(record["system_name"], "Shipping Portal")
        self.assertEqual(record["resource_category_name"], "Social Publishing")

    def test_default_resource_categories_are_seeded_and_used_as_fallback(self):
        categories = self.store.list_resource_categories()
        names = {category["name"] for category in categories}

        self.assertIn("Business Applications", names)
        self.assertIn("Social Media", names)
        self.assertTrue(all(system["resource_category_name"] for system in self.store.list_systems()))

        system = self.store.create_system(
            {
                "name": "No Explicit Category",
                "category": "network",
                "owner": "IT Security",
                "risk_level": "privileged",
            },
            actor="Test Supervisor",
            role="Supervisor",
        )

        self.assertEqual(system["resource_category_name"], "Network Access")

    def test_custom_system_urls_must_be_http_urls(self):
        with self.assertRaises(ApiError) as context:
            self.store.create_system(
                {
                    "name": "Bad URL System",
                    "product_name": "Bad URL Product",
                    "application_url": "shipping.example.local",
                    "category": "software",
                    "owner": "IT Security",
                    "risk_level": "standard",
                },
                actor="Test Admin",
                role="Admin",
            )

        self.assertEqual(context.exception.status, 400)
        self.assertIn("Application URL", context.exception.message)

    def test_terminating_employee_routes_active_access_to_removal(self):
        employee = self.store.create_employee(
            {
                "employee_id": "E-9999",
                "name": "Taylor Kim",
                "email": "taylor.kim@example.local",
                "department": "Operations",
                "location": "HQ",
            },
            actor="Test Admin",
            role="Admin",
        )
        system = self.store.list_systems()[0]
        self.store.create_access_record(
            {
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_level": "User",
                "access_type": "user",
                "status": "active",
                "business_reason": "Needs access for daily work.",
                "owner": system["owner"],
            },
            actor="Test Admin",
            role="Admin",
        )

        self.store.update_employee(
            employee["id"],
            {"status": "terminated"},
            actor="Test HR",
            role="HR",
        )

        detail = self.store.employee_detail(employee["id"])
        statuses = {record["status"] for record in detail["access"]}
        self.assertEqual(detail["employee"]["status"], "terminated")
        self.assertIn("removal_pending", statuses)

    def test_csv_import_matches_active_employee_and_flags_unknown_account(self):
        company_vpn = next(system for system in self.store.list_systems() if system["name"] == "Company VPN")
        result = self.store.import_accounts(
            {
                "system_id": company_vpn["id"],
                "source_name": "Unit test export",
                "csv_text": "\n".join(
                    [
                        "employee_id,email,name,account,role,access_type",
                        "E-1003,priya.shah@example.local,Priya Shah,pshah-vpn,Power User,user",
                        ",unknown@example.local,Unknown User,unknown.admin,Administrator,admin",
                    ]
                ),
            },
            actor="Test Admin",
            role="Admin",
        )

        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["matched_rows"], 1)
        self.assertEqual(result["unmatched_rows"], 1)
        self.assertEqual(result["created_access_records"], 1)

        records = self.store.list_access_records({"q": "Priya"})
        imported = [record for record in records if record["source_import_run_id"] == result["id"]]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["status"], "unknown")

    def test_removed_access_requires_evidence(self):
        record = self.store.list_access_records({})[0]

        with self.assertRaises(ApiError) as context:
            self.store.update_access_record(
                record["id"],
                {"status": "removed"},
                actor="Test HR",
                role="HR",
            )

        self.assertEqual(context.exception.status, 400)

        updated = self.store.update_access_record(
            record["id"],
            {"status": "removed", "removal_evidence": "Ticket IT-1234 confirmed account disabled."},
            actor="Test HR",
            role="HR",
        )
        self.assertEqual(updated["status"], "removed")
        self.assertEqual(updated["removal_evidence"], "Ticket IT-1234 confirmed account disabled.")

    def test_create_removed_access_requires_evidence(self):
        employee = self.store.list_employees()[0]
        system = self.store.list_systems()[0]

        with self.assertRaises(ApiError) as context:
            self.store.create_access_record(
                {
                    "employee_id": employee["id"],
                    "system_id": system["id"],
                    "access_level": "User",
                    "access_type": "user",
                    "status": "removed",
                    "business_reason": "Historical cleanup.",
                    "owner": system["owner"],
                },
                actor="Test Admin",
                role="Admin",
            )

        self.assertEqual(context.exception.status, 400)
        self.assertIn("Removal evidence is required", context.exception.message)

    def test_ad_sync_creates_users_and_flags_disabled_accounts(self):
        result = self.store.sync_ad_users(
            {
                "source_name": "Unit test AD",
                "format": "csv",
                "directory_text": "\n".join(
                    [
                        "EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName",
                        "E-2001,Casey Nguyen,casey.nguyen@example.local,IT,HQ,Sam Patel,TRUE,guid-2001,casey.nguyen@example.local,cnguyen",
                        "E-2002,Robin Gray,robin.gray@example.local,Finance,HQ,Riley Brooks,FALSE,guid-2002,robin.gray@example.local,rgray",
                    ]
                ),
            },
            actor="Test Admin",
            role="Admin",
        )

        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["created_users"], 2)
        self.assertEqual(result["disabled_users"], 1)

        employees = self.store.list_employees()
        disabled = next(employee for employee in employees if employee["employee_id"] == "E-2002")
        self.assertEqual(disabled["source"], "active_directory")
        self.assertEqual(disabled["ad_enabled"], 0)
        self.assertIsNotNone(disabled["ad_disabled_flagged_at"])
        self.assertEqual(self.store.summary()["adDisabledUsers"], 1)

    def test_ad_sync_preserves_admin_overridden_fields(self):
        employee = self.store.create_employee(
            {
                "employee_id": "E-3001",
                "name": "Local Display",
                "email": "local.display@example.local",
                "department": "Special Projects",
                "location": "Field Office",
                "manager": "Local Manager",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.store.update_employee(
            employee["id"],
            {
                "admin_override": True,
                "admin_notes": "Use local department until HR cleanup is complete.",
            },
            actor="Test Admin",
            role="Admin",
        )

        result = self.store.sync_ad_users(
            {
                "source_name": "Unit test AD",
                "format": "json",
                "directory_text": """
                [
                  {
                    "EmployeeID": "E-3001",
                    "Name": "Directory Display",
                    "Mail": "directory.display@example.local",
                    "Department": "Accounting",
                    "Office": "HQ",
                    "Manager": "Directory Manager",
                    "Enabled": false,
                    "ObjectGUID": "guid-3001",
                    "UserPrincipalName": "directory.display@example.local",
                    "SamAccountName": "ddisplay"
                  }
                ]
                """,
            },
            actor="Test Admin",
            role="Admin",
        )

        self.assertEqual(result["updated_users"], 1)
        self.assertEqual(result["preserved_overrides"], 1)

        detail = self.store.employee_detail(employee["id"])
        updated = detail["employee"]
        self.assertEqual(updated["name"], "Local Display")
        self.assertEqual(updated["email"], "local.display@example.local")
        self.assertEqual(updated["department"], "Special Projects")
        self.assertEqual(updated["ad_enabled"], 0)
        self.assertEqual(updated["ad_sam_account_name"], "ddisplay")

    def test_access_request_approval_creates_expiring_access_record(self):
        employee = self.store.list_employees()[0]
        system = self.store.list_systems()[0]

        request = self.store.create_access_request(
            {
                "requester": "Unit Test",
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_level": "Temporary Admin",
                "access_type": "admin",
                "business_reason": "Temporary incident response access.",
                "expiration_date": "2026-07-15",
            },
            actor="Test HR",
            role="HR",
        )
        decided = self.store.decide_access_request(
            request["id"],
            {"decision": "approve", "approver": "Test Reviewer"},
            actor="Test Reviewer",
            role="Reviewer",
        )

        self.assertEqual(decided["status"], "fulfilled")
        self.assertIsNotNone(decided["created_access_record_id"])
        record = self.store.get_access_record(decided["created_access_record_id"])
        self.assertEqual(record["expires_at"], "2026-07-15")
        self.assertEqual(record["status"], "active")

    def test_access_request_rejects_unsupported_access_type(self):
        employee = self.store.list_employees()[0]
        system = self.store.list_systems()[0]

        with self.assertRaises(ApiError) as context:
            self.store.create_access_request(
                {
                    "requester": "Unit Test",
                    "employee_id": employee["id"],
                    "system_id": system["id"],
                    "access_level": "Unsupported",
                    "access_type": "domain_adminish",
                    "business_reason": "Invalid request should fail before approval.",
                },
                actor="Test HR",
                role="HR",
            )

        self.assertEqual(context.exception.status, 400)
        self.assertIn("Unsupported access type", context.exception.message)

    def test_disabled_ad_user_queue_routes_access_to_removal(self):
        employee = self.store.list_employees()[0]
        system = self.store.list_systems()[0]
        self.store.update_employee(
            employee["id"],
            {"admin_override": True},
            actor="Test Admin",
            role="Admin",
        )
        self.store.sync_ad_users(
            {
                "source_name": "Disabled user sync",
                "format": "csv",
                "directory_text": "\n".join(
                    [
                        "EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName",
                        f"{employee['employee_id']},{employee['name']},{employee['email']},{employee['department']},{employee['location']},{employee['manager'] or ''},FALSE,guid-disabled,{employee['email']},disableduser",
                    ]
                ),
            },
            actor="Test Admin",
            role="Admin",
        )
        self.store.create_access_record(
            {
                "employee_id": employee["id"],
                "system_id": system["id"],
                "access_level": "VPN User",
                "access_type": "user",
                "status": "active",
                "business_reason": "Needs VPN.",
                "owner": system["owner"],
            },
            actor="Test Admin",
            role="Admin",
        )

        queue = self.store.disabled_access_queue()
        self.assertGreaterEqual(len(queue), 1)
        result = self.store.route_disabled_access_to_removal(actor="Test Admin", role="Admin")
        self.assertGreaterEqual(result["routed"], 1)
        statuses = {record["status"] for record in self.store.employee_detail(employee["id"])["access"]}
        self.assertIn("removal_pending", statuses)

    def test_governance_assets_backup_and_settings(self):
        system = self.store.list_systems()[0]
        employee = self.store.list_employees()[0]

        shared = self.store.create_shared_account(
            {
                "system_id": system["id"],
                "account_name": "breakglass-test",
                "owner": "IT Security",
                "business_reason": "Emergency recovery.",
                "mfa_enabled": False,
                "rotation_due_at": "2026-01-01",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(shared["account_name"], "breakglass-test")
        self.assertTrue(any(finding["title"] == "Shared account has no MFA evidence" for finding in self.store.risk_findings()))

        credential = self.store.create_physical_credential(
            {
                "employee_id": employee["id"],
                "location": "HQ",
                "credential_type": "badge",
                "credential_identifier": "Badge-1",
                "status": "active",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(credential["credential_type"], "badge")

        campaign = self.store.create_review_campaign(
            {
                "name": "Quarterly review",
                "owner": "IT Security",
                "due_date": "2026-07-01",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(campaign["status"], "open")

        connector = self.store.create_connector(
            {
                "name": "Microsoft 365",
                "connector_type": "microsoft_365",
                "owner": "IT Security",
                "status": "planned",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(connector["status"], "planned")

        settings = self.store.update_auth_settings(
            {
                "provider": "active_directory",
                "login_required": True,
                "admin_group": "DOMAIN\\AccessRegister-Admins",
                "supervisor_group": "DOMAIN\\AccessRegister-Supervisors",
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(settings["provider"], "active_directory")
        self.assertEqual(settings["login_required"], 1)
        self.assertEqual(settings["supervisor_group"], "DOMAIN\\AccessRegister-Supervisors")

        backup = self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")
        self.assertEqual(backup["status"], "complete")
        self.assertTrue(Path(backup["backup_path"]).exists())

    def test_backup_paths_are_unique_and_retention_is_validated(self):
        first = self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")
        second = self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")

        self.assertNotEqual(first["backup_path"], second["backup_path"])
        self.assertTrue(Path(first["backup_path"]).exists())
        self.assertTrue(Path(second["backup_path"]).exists())

        redacted = self.store.list_backups(include_paths=False)
        self.assertIsNone(redacted[0]["backup_path"])
        self.assertFalse(redacted[0]["path_visible"])

        with self.assertRaises(ApiError) as context:
            self.store.run_backup({"retention_days": 0}, actor="Test Admin", role="Admin")

        self.assertEqual(context.exception.status, 400)
        self.assertIn("Backup retention must be at least 1", context.exception.message)

    def test_backup_retention_prunes_expired_backup_files_after_successful_backup(self):
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        expired_backup = backup_dir / "access_register_expired.db"
        expired_backup.write_bytes(b"expired backup")
        old_created_at = (
            datetime.now(timezone.utc) - timedelta(days=45)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        with self.store.session() as conn:
            expired_id = conn.execute(
                """
                INSERT INTO backup_runs (
                    backup_path, status, retention_days, size_bytes, error, created_at
                )
                VALUES (?, 'complete', 30, ?, NULL, ?)
                """,
                [str(expired_backup), expired_backup.stat().st_size, old_created_at],
            ).lastrowid

        current = self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")

        self.assertEqual(current["status"], "complete")
        self.assertTrue(Path(current["backup_path"]).exists())
        self.assertFalse(expired_backup.exists())
        with self.store.session() as conn:
            expired_row = conn.execute("SELECT * FROM backup_runs WHERE id = ?", [expired_id]).fetchone()
        self.assertIsNotNone(expired_row["pruned_at"])
        self.assertIn("Pruned 1 expired backup(s)", self.store.audit_log()[0]["summary"])

    def test_backup_retention_does_not_prune_paths_outside_backup_directory(self):
        outside_backup = self.db_path.parent / "outside-retention.db"
        outside_backup.write_bytes(b"must not be deleted")
        old_created_at = (
            datetime.now(timezone.utc) - timedelta(days=45)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        with self.store.session() as conn:
            outside_id = conn.execute(
                """
                INSERT INTO backup_runs (
                    backup_path, status, retention_days, size_bytes, error, created_at
                )
                VALUES (?, 'complete', 30, ?, NULL, ?)
                """,
                [str(outside_backup), outside_backup.stat().st_size, old_created_at],
            ).lastrowid

        with contextlib.redirect_stderr(io.StringIO()):
            current = self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")

        self.assertEqual(current["status"], "complete")
        self.assertTrue(outside_backup.exists())
        with self.store.session() as conn:
            outside_row = conn.execute("SELECT * FROM backup_runs WHERE id = ?", [outside_id]).fetchone()
        self.assertIsNone(outside_row["pruned_at"])

    def test_audit_csv_escapes_spreadsheet_formula_cells(self):
        dangerous_actors = [
            '=HYPERLINK("http://example.invalid","click")',
            "+SUM(1,1)",
            "-2+3",
            "@SUM(1,1)",
            "\t=SUM(1,1)",
            "\r=SUM(1,1)",
            "\n=SUM(1,1)",
        ]

        for actor in dangerous_actors:
            with self.subTest(actor=repr(actor)):
                self.store.run_backup({"retention_days": 30}, actor=actor, role="Admin")
                rows = list(csv.DictReader(io.StringIO(self.store.audit_log_csv())))

                self.assertEqual(self.store.audit_log()[0]["actor"], actor)
                self.assertEqual(rows[0]["actor"], f"'{actor}")

        self.store.run_backup({"retention_days": 30}, actor="Test Admin", role="Admin")
        rows = list(csv.DictReader(io.StringIO(self.store.audit_log_csv())))
        self.assertEqual(rows[0]["actor"], "Test Admin")

    def test_scheduled_ad_sync_replays_saved_payload(self):
        settings = self.store.update_ad_sync_settings(
            {
                "enabled": True,
                "format": "csv",
                "interval_hours": 1,
                "next_run_at": "2020-01-01T00:00:00Z",
                "directory_text": "\n".join(
                    [
                        "EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName",
                        "E-4001,Scheduled User,scheduled.user@example.local,IT,HQ,Sam Patel,TRUE,guid-4001,scheduled.user@example.local,suser",
                    ]
                ),
            },
            actor="Test Admin",
            role="Admin",
        )
        self.assertEqual(settings["enabled"], 1)

        result = self.store.run_scheduled_ad_sync(actor="Scheduler", role="Admin", force=False)
        self.assertFalse(result["skipped"])
        employees = self.store.list_employees()
        self.assertTrue(any(employee["employee_id"] == "E-4001" for employee in employees))
        self.assertEqual(self.store.get_ad_sync_settings()["last_status"], "complete")

    def test_scheduled_ad_sync_records_failure_status_and_audit(self):
        self.store.update_ad_sync_settings(
            {
                "enabled": True,
                "format": "json",
                "interval_hours": 1,
                "next_run_at": "2020-01-01T00:00:00Z",
                "directory_text": "{invalid-json",
            },
            actor="Test Admin",
            role="Admin",
        )

        with self.assertRaises(ApiError) as context:
            self.store.run_scheduled_ad_sync(actor="Scheduler", role="Admin", force=False)

        self.assertEqual(context.exception.status, 400)
        settings = self.store.get_ad_sync_settings()
        self.assertTrue(settings["last_status"].startswith("failed: AD JSON payload is invalid"))
        self.assertIsNotNone(settings["last_run_at"])
        notifications = self.store.list_notifications()
        notification = next(note for note in notifications if note["source_type"] == "scheduled_ad_sync")
        self.assertEqual(notification["subject"], "Scheduled AD sync failed")
        self.assertEqual(notification["severity"], "high")
        self.assertEqual(notification["status"], "pending")
        self.assertIn("AD JSON payload is invalid", notification["body"])
        audit_summaries = "\n".join(entry["summary"] for entry in self.store.audit_log())
        self.assertIn("Scheduled AD sync failed: AD JSON payload is invalid", audit_summaries)


if __name__ == "__main__":
    unittest.main()
