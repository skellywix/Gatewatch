import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import Store


class ScenarioMatrixTests(unittest.TestCase):
    def test_employee_lifecycle_matrix_covers_1000_scenarios(self):
        statuses = ("active", "disabled", "terminated")
        sources = ("HR", "Manager", "IT", "Employee", "Other")
        departments = ("Operations", "IT", "Finance", "Retail", "Security")
        profiles = (
            {"vpn_access": "Required"},
            {"core_banking": True},
            {"branch": "HQ", "corporate_card": False},
            {"software_access": "Email\nVPN"},
        )
        scenarios_run = 0

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempdir:
            db_path = Path(tempdir) / "gatewatch-scenarios.db"
            store = Store(db_path)
            store.init()

            for scenario_id in range(1000):
                status = statuses[scenario_id % len(statuses)]
                source = sources[scenario_id % len(sources)]
                department = departments[scenario_id % len(departments)]
                flags = {
                    "request_received": bool(scenario_id & 1),
                    "manager_approved": bool(scenario_id & 2),
                    "it_provisioned": bool(scenario_id & 4),
                    "employee_notified": bool(scenario_id & 8),
                }
                payload = {
                    "employee_id": f"SCN-{scenario_id:04d}",
                    "name": f"Scenario User {scenario_id:04d}",
                    "email": f"scenario.user.{scenario_id:04d}@example.test",
                    "phone": f"555-{scenario_id:04d}",
                    "department": department,
                    "title": f"{department} Specialist",
                    "location": "HQ" if scenario_id % 2 == 0 else "Branch",
                    "manager": f"Manager {scenario_id % 17}",
                    "status": status,
                    "request_source": source,
                    "access_needed": f"{source} scenario {scenario_id}",
                    "access_profile": profiles[scenario_id % len(profiles)],
                    "notes": f"Generated coverage scenario {scenario_id}",
                    **flags,
                }

                with self.subTest(scenario=scenario_id):
                    created = store.create_employee(payload, actor=f"Scenario Actor {scenario_id % 11}")
                    self.assertEqual(created["employee_id"], payload["employee_id"])
                    self.assertEqual(created["status"], status)
                    self.assertEqual(created["access_profile"], profiles[scenario_id % len(profiles)])

                    search_result = store.list_employees(f"SCN-{scenario_id:04d}")
                    self.assertEqual(len(search_result), 1)
                    self.assertEqual(search_result[0]["id"], created["id"])

                    updated = store.update_employee(
                        created["id"],
                        {
                            "phone": f"555-9{scenario_id:04d}",
                            "notes": f"Updated coverage scenario {scenario_id}",
                            "request_received": True,
                            "manager_approved": scenario_id % 3 != 0,
                            "it_provisioned": scenario_id % 5 == 0,
                            "employee_notified": scenario_id % 7 == 0,
                        },
                        actor="Scenario Updater",
                    )
                    self.assertEqual(updated["phone"], f"555-9{scenario_id:04d}")
                    self.assertEqual(updated["request_received"], 1)

                    if scenario_id % 25 == 0:
                        deleted = store.delete_employee(created["id"], actor="Scenario Cleanup")
                        self.assertTrue(deleted["deleted"])
                        restored = store.restore_employee(created["id"], actor="Scenario Restore")
                        self.assertFalse(restored["deleted"])

                    scenarios_run += 1

            reopened = Store(db_path)
            reopened.init()
            summary = reopened.summary()
            with sqlite3.connect(db_path) as conn:
                audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

        self.assertEqual(scenarios_run, 1000)
        self.assertEqual(summary["total"], 1000)
        self.assertGreaterEqual(summary["active"], 300)
        self.assertGreaterEqual(summary["disabled"], 300)
        self.assertGreaterEqual(summary["terminated"], 300)
        self.assertGreaterEqual(audit_count, 2000)


if __name__ == "__main__":
    unittest.main()
