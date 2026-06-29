import argparse
import contextlib
import io
from unittest import mock
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import verify  # noqa: E402


class VerifyScriptTests(unittest.TestCase):
    def test_positive_int_accepts_only_positive_numbers(self):
        self.assertEqual(verify.positive_int("3"), 3)

        for value in ["0", "-1", "abc"]:
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    verify.positive_int(value)

    def test_default_checks_are_the_local_verification_contract(self):
        selected = verify.checks(include_docker=False)
        names = [check.name for check in selected]

        self.assertEqual(
            names,
            [
                "Python compile",
                "Backend and UI smoke tests",
                "Frontend JavaScript syntax",
            ],
        )
        self.assertIn("app.py", selected[0].command)
        self.assertEqual(selected[0].command[0], sys.executable)
        self.assertEqual(selected[0].display_command[0], "python")
        self.assertIn("Compile all Python", selected[0].description)
        self.assertIn("scripts", selected[0].command)
        self.assertIn("tests", selected[0].command)
        self.assertIn("tests", selected[1].command)
        self.assertEqual(selected[2].requires, "node")

    def test_docker_check_is_opt_in(self):
        default_names = [check.name for check in verify.checks(include_docker=False)]
        docker_checks = verify.checks(include_docker=True)

        self.assertNotIn("Production Docker build", default_names)
        self.assertEqual(docker_checks[-1].name, "Production Docker build")
        self.assertEqual(docker_checks[-1].requires, "docker")
        self.assertEqual(verify.skipped_checks(include_docker=False), ["Production Docker build (use --docker)"])
        self.assertEqual(verify.skipped_checks(include_docker=True), [])

    def test_parse_args_supports_listing_selected_checks(self):
        args = verify.parse_args(["--repeat", "2", "--docker", "--list"])

        self.assertEqual(args.repeat, 2)
        self.assertTrue(args.docker)
        self.assertTrue(args.list)

    def test_print_checklist_shows_commands_without_running_them(self):
        selected = verify.checks(include_docker=True)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            verify.print_checklist(selected, repeat=2)

        text = output.getvalue()
        self.assertIn("Gatewatch verification checklist (4 check(s) x 2 run(s))", text)
        self.assertIn("1. Python compile", text)
        self.assertIn("$ python -m compileall -q app.py scripts tests", text)
        self.assertIn("$ docker build -t gatewatch-ci .", text)
        self.assertNotIn(sys.executable, text)

    def test_print_checklist_shows_optional_skips(self):
        selected = verify.checks(include_docker=False)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            verify.print_checklist(selected, repeat=1, skipped=verify.skipped_checks(include_docker=False))

        text = output.getvalue()
        self.assertIn("Skipped optional check(s):", text)
        self.assertIn("Production Docker build (use --docker)", text)

    def test_missing_executables_fail_before_running_checks(self):
        selected = [
            verify.Check(
                "Missing tool",
                ["definitely-not-a-real-gatewatch-tool"],
                "Exercise missing executable handling.",
                requires="definitely-not-a-real-gatewatch-tool",
            )
        ]

        with self.assertRaises(SystemExit) as context:
            verify.ensure_executables(selected)

        self.assertIn("Missing required executable", str(context.exception))

    def test_run_check_reports_nonzero_exit_code(self):
        check = verify.Check("Failing check", [sys.executable, "-c", "raise SystemExit(7)"], "Fails on purpose.")

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as context:
                verify.run_check(check, index=1, total=1, cycle=1, repeat=1)

        self.assertIn("Failing check failed with exit code 7", str(context.exception))

    def test_run_check_reports_missing_output_pipe(self):
        check = verify.Check("No output pipe", ["fake-command"], "Simulates an impossible Popen shape.")

        with mock.patch("scripts.verify.subprocess.Popen") as popen:
            popen.return_value.stdout = None
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as context:
                    verify.run_check(check, index=1, total=1, cycle=1, repeat=1)

        self.assertIn("No output pipe failed to expose command output", str(context.exception))

    def test_run_check_reports_startup_errors(self):
        check = verify.Check("Missing executable", ["missing-command"], "Simulates Popen startup failure.")

        with mock.patch("scripts.verify.subprocess.Popen", side_effect=OSError("not found")):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as context:
                    verify.run_check(check, index=1, total=1, cycle=1, repeat=1)

        self.assertIn("Missing executable could not start: not found", str(context.exception))


if __name__ == "__main__":
    unittest.main()
