import subprocess
import shutil
import unittest
import os
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def bash_or_skip(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not available")
        probe = subprocess.run([bash, "--version"], capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            self.skipTest(f"bash is not runnable: {probe.stderr.strip() or probe.stdout.strip()}")
        return bash

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
        self.assertIn("--validate-paths-only", script)
        self.assertIn("/dev/tty", script)
        self.assertIn("Install directory", script)
        self.assertIn("systemd service name", script)
        self.assertIn("--service-name", script)
        self.assertIn("--service-user", script)
        self.assertIn("--entra-tenant-id", script)
        self.assertIn("--entra-client-id", script)
        self.assertIn("--entra-client-secret", script)
        self.assertIn("--entra-redirect-uri", script)
        self.assertIn("--admin-group-canonical", script)
        self.assertIn("--supervisor-group-canonical", script)
        self.assertIn("GATEWATCH_ADMIN_GROUP_CANONICAL", script)
        self.assertIn("GATEWATCH_SUPERVISOR_GROUP_CANONICAL", script)
        self.assertIn("GATEWATCH_CONFIG_FILE", script)
        self.assertIn("write_env_var", script)
        self.assertIn("reject_system_root_path", script)
        self.assertIn("must not be a system root directory", script)
        self.assertIn("reject_parent_path_segments", script)
        self.assertIn("must not contain parent directory segments", script)
        self.assertIn("physical_directory_path", script)
        self.assertIn("must not traverse symlinked parent directories", script)
        self.assertIn("Data and environment directories must not be inside the install web directory", script)
        self.assertIn("Install, data, and environment directories must not overlap", script)
        self.assertIn("Configure Microsoft Entra ID SSO and directory sync now?", script)
        self.assertIn("GATEWATCH_ENTRA_TENANT_ID", script)
        self.assertIn("/opt/gatewatch", script)
        self.assertIn("/var/lib/gatewatch", script)
        self.assertIn('SERVICE_NAME="gatewatch"', script)
        self.assertIn('SERVICE_UNIT="${SERVICE_NAME}.service"', script)
        self.assertIn("ensure_apt_packages ca-certificates tar curl python3", script)
        self.assertIn("ProtectSystem=full", script)
        self.assertIn("ReadWritePaths=${DATA_DIR} ${ENV_DIR}", script)
        self.assertIn('chmod 0660 "${ENV_FILE}"', script)
        self.assertIn("Refusing non-loopback host", script)
        self.assertIn("Health check passed", script)
        self.assertIn("sudo bash scripts/install-ubuntu.sh", readme)

    def test_ubuntu_installer_has_valid_bash_syntax(self):
        bash = self.bash_or_skip()
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
        bash = self.bash_or_skip()
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
        self.assertIn("--entra-tenant-id ID", result.stdout)
        self.assertIn("--admin-group-canonical GROUP", result.stdout)
        self.assertIn("--supervisor-group-canonical GROUP", result.stdout)
        self.assertIn("--non-interactive", result.stdout)

    def test_ubuntu_installer_validates_paths_before_privileged_file_operations(self):
        if os.name == "nt":
            self.skipTest("POSIX path validation requires POSIX paths")
        bash = self.bash_or_skip()
        installer = REPO_ROOT / "scripts" / "install-ubuntu.sh"

        with tempfile.TemporaryDirectory() as tempdir:
            base = Path(tempdir).resolve()
            valid_paths = {
                "--install-dir": base / "opt" / "gatewatch",
                "--data-dir": base / "var" / "lib" / "gatewatch",
                "--env-dir": base / "etc" / "gatewatch",
            }

            def run_with_paths(extra_paths, *, env=None):
                paths = {**valid_paths, **extra_paths}
                command = [bash, str(installer), "--validate-paths-only", "--yes"]
                for flag, value in paths.items():
                    command.extend([flag, str(value)])
                return subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=env,
                )

            valid = run_with_paths({})
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertIn("Install path validation passed", valid.stdout)

            symlink = base / "root-link"
            symlink.symlink_to("/", target_is_directory=True)

            cases = [
                ({"--install-dir": "/"}, "must not be a system root directory"),
                ({"--install-dir": f"{base}/safe/../gatewatch"}, "must not contain parent directory segments"),
                ({"--install-dir": symlink / "opt" / "gatewatch"}, "must not traverse symlinked parent directories"),
                (
                    {"--data-dir": base / "opt" / "gatewatch" / "web" / "data"},
                    "must not be inside the install web directory",
                ),
                ({"--data-dir": base / "opt" / "gatewatch" / "data"}, "must not overlap"),
            ]

            for extra_paths, expected_error in cases:
                with self.subTest(expected_error=expected_error):
                    result = run_with_paths(extra_paths)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_error, result.stderr)

    def test_obsolete_windows_and_ad_install_paths_are_removed(self):
        obsolete_paths = [
            "Deploy-Gatewatch.cmd",
            "Deploy-Gatewatch.ps1",
            "scripts/install-gatewatch-production.ps1",
            "scripts/repair-gatewatch-deployment.ps1",
            "scripts/sync-active-directory.ps1",
            "docker/ad-test",
            "docker/ad-sync-test",
            "docker/vsphere",
        ]

        for relative_path in obsolete_paths:
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())

    def test_dockerfile_uses_new_gatewatch_env_names(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("GATEWATCH_DB=/data/gatewatch.db", dockerfile)
        self.assertIn("GATEWATCH_CONFIG_FILE=/data/gatewatch.env", dockerfile)
        self.assertIn("GATEWATCH_ALLOW_INSECURE_NETWORK=1", dockerfile)
        self.assertNotIn("ACCESS_REGISTER_AUTH_MODE", dockerfile)

    def test_dockerignore_excludes_local_runtime_artifacts(self):
        patterns = {
            line.strip()
            for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

        for pattern in [
            ".git",
            ".agents",
            ".codex",
            "__pycache__",
            ".pytest_cache",
            "output",
            "data",
            "*.log",
            "*.db",
            "*.db-*",
            "*.sqlite",
        ]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, patterns)

    def test_full_test_proxy_lab_documents_browser_sso_smoke(self):
        compose = (REPO_ROOT / "docker" / "full-test" / "compose.yaml").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "docker" / "full-test" / "README.md").read_text(encoding="utf-8")
        proxy = (REPO_ROOT / "docker" / "full-test" / "trusted_proxy.py").read_text(encoding="utf-8")
        smoke = (REPO_ROOT / "docker" / "full-test" / "browser_sso_smoke.py").read_text(encoding="utf-8")
        verify_script = (REPO_ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")

        self.assertIn("GATEWATCH_AUTH_MODE: \"trusted_proxy\"", compose)
        self.assertIn("GATEWATCH_PROXY_SECRET", compose)
        self.assertIn("X-Gatewatch-Proxy-Secret", proxy)
        self.assertIn("X-Remote-Groups", proxy)
        self.assertIn("browser-smoke", compose)
        self.assertIn("canModifyEmployees", smoke)
        self.assertIn("Operations Console", smoke)
        self.assertIn("docker compose --env-file docker/full-test/.env.example", readme)
        self.assertIn("--docker-full-test", verify_script)

    def test_remote_container_deploy_script_is_scoped_and_repeatable(self):
        script_path = REPO_ROOT / "scripts" / "deploy-container.sh"
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")
        script = script_path.read_text(encoding="utf-8")

        self.assertTrue(script_path.exists())
        self.assertIn("scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP", readme)
        self.assertIn("Remote Container Rollout", rollout)
        self.assertIn('DEFAULT_SOURCE_URL="https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"', script)
        self.assertIn('docker rm -f "${GATEWATCH_CONTAINER_NAME}"', script)
        self.assertIn('docker volume rm "${GATEWATCH_VOLUME_NAME}"', script)
        self.assertIn("--read-only", script)
        self.assertIn("-e GATEWATCH_CONFIG_FILE=/data/gatewatch.env", script)
        self.assertIn("GATEWATCH_SUPERVISOR_GROUP_CANONICAL", script)
        self.assertIn("--cap-drop ALL", script)
        self.assertIn("--security-opt no-new-privileges", script)
        self.assertIn("Health check passed", script)
        self.assertIn("emit_remote_script | ssh", script)
        self.assertIn("over SSH", script)
        self.assertNotIn("remote_env", script)
        self.assertNotIn("192.168.4.79", script)

    def test_remote_container_deploy_script_has_valid_bash_syntax(self):
        bash = self.bash_or_skip()
        script = (REPO_ROOT / "scripts" / "deploy-container.sh").read_text(encoding="utf-8")
        result = subprocess.run(
            [bash, "-n", "-s"],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_remote_container_deploy_validates_port_before_ssh(self):
        bash = self.bash_or_skip()
        script = REPO_ROOT / "scripts" / "deploy-container.sh"
        for port in ["0", "65536", "abc", "70000"]:
            with self.subTest(port=port):
                result = subprocess.run(
                    [bash, str(script), "--validate-only", "--target", "example.invalid", "--host-port", port],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--host-port must be a number from 1 to 65535", result.stderr)
                self.assertNotIn("Deploying Gatewatch", result.stdout)

        valid = subprocess.run(
            [bash, str(script), "--validate-only", "--target", "example.invalid", "--host-port", "65535"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertIn("Deploy configuration validation passed", valid.stdout)

        env = {**os.environ, "GATEWATCH_HOST_PORT": "70000"}
        env_result = subprocess.run(
            [bash, str(script), "--validate-only", "--target", "example.invalid"],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        self.assertNotEqual(env_result.returncode, 0)
        self.assertIn("--host-port must be a number from 1 to 65535", env_result.stderr)


if __name__ == "__main__":
    unittest.main()
