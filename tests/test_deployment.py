import subprocess
import shutil
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_ubuntu_installer_is_the_one_click_path(self):
        installer = REPO_ROOT / "scripts" / "install-ubuntu.sh"
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        script = installer.read_text(encoding="utf-8")

        self.assertTrue(installer.exists())
        self.assertIn("curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash", readme)
        self.assertIn("DEFAULT_SOURCE_URL=", script)
        self.assertIn("https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz", script)
        self.assertIn("Downloading Gatewatch source", script)
        self.assertIn("curl -fsSL", script)
        self.assertIn("--source-url", script)
        self.assertIn("--yes, --non-interactive", script)
        self.assertIn("/dev/tty", script)
        self.assertIn("Install directory", script)
        self.assertIn("systemd service name", script)
        self.assertIn("--service-name", script)
        self.assertIn("--service-user", script)
        self.assertIn("/opt/gatewatch", script)
        self.assertIn("/var/lib/gatewatch", script)
        self.assertIn('SERVICE_NAME="gatewatch"', script)
        self.assertIn('SERVICE_UNIT="${SERVICE_NAME}.service"', script)
        self.assertIn("ensure_apt_packages ca-certificates tar curl python3", script)
        self.assertIn("ProtectSystem=full", script)
        self.assertIn("ReadWritePaths=${DATA_DIR}", script)
        self.assertIn("Refusing non-loopback host", script)
        self.assertIn("Health check passed", script)
        self.assertIn("sudo bash scripts/install-ubuntu.sh", readme)

    def test_ubuntu_installer_has_valid_bash_syntax(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not available")
        probe = subprocess.run([bash, "--version"], capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            self.skipTest(f"bash is not runnable: {probe.stderr.strip() or probe.stdout.strip()}")
        script = (REPO_ROOT / "scripts" / "install-ubuntu.sh").read_text(encoding="utf-8")
        result = subprocess.run(
            [bash, "-n", "-s"],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ubuntu_installer_help_does_not_require_root(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not available")
        probe = subprocess.run([bash, "--version"], capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            self.skipTest(f"bash is not runnable: {probe.stderr.strip() or probe.stdout.strip()}")
        installer = REPO_ROOT / "scripts" / "install-ubuntu.sh"
        result = subprocess.run(
            [bash, str(installer), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("One-line install:", result.stdout)
        self.assertIn("--source-url URL", result.stdout)
        self.assertIn("--non-interactive", result.stdout)

    def test_obsolete_windows_and_ad_install_paths_are_removed(self):
        obsolete_paths = [
            "Deploy-Gatewatch.cmd",
            "Deploy-Gatewatch.ps1",
            "scripts/install-gatewatch-production.ps1",
            "scripts/repair-gatewatch-deployment.ps1",
            "scripts/sync-active-directory.ps1",
            "docker/ad-test",
            "docker/ad-sync-test",
            "docker/full-test",
            "docker/vsphere",
        ]

        for relative_path in obsolete_paths:
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())

    def test_dockerfile_uses_new_gatewatch_env_names(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("GATEWATCH_DB=/data/gatewatch.db", dockerfile)
        self.assertIn("GATEWATCH_ALLOW_INSECURE_NETWORK=1", dockerfile)
        self.assertNotIn("ACCESS_REGISTER_AUTH_MODE", dockerfile)


if __name__ == "__main__":
    unittest.main()
