import json
import subprocess
import shutil
import unittest
import os
import shlex
import sys
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

    def bash_is_wsl(self, bash):
        if os.name != "nt":
            return False
        probe = subprocess.run(
            [bash, "-lc", "uname -r"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "microsoft" in probe.stdout.lower()

    def bash_script_path(self, bash, script):
        if not self.bash_is_wsl(bash):
            return str(script)
        resolved = Path(script).resolve()
        drive = resolved.drive.rstrip(":").lower()
        parts = resolved.parts[1:]
        return f"/mnt/{drive}/{'/'.join(parts)}"

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
        self.assertIn("--auth-mode", script)
        self.assertIn("--proxy-secret", script)
        self.assertIn("GATEWATCH_ADMIN_GROUP_CANONICAL", script)
        self.assertIn("GATEWATCH_SUPERVISOR_GROUP_CANONICAL", script)
        self.assertIn("GATEWATCH_AUTH_MODE", script)
        self.assertIn("GATEWATCH_PROXY_SECRET", script)
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
        self.assertIn("Trust identity headers from a protected reverse proxy?", script)
        self.assertIn("GATEWATCH_ENTRA_TENANT_ID", script)
        self.assertIn("--proxy-secret must be at least 16 characters", script)
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
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=15,
        )
        output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, output)

    def test_ubuntu_installer_help_does_not_require_root(self):
        bash = self.bash_or_skip()
        installer = REPO_ROOT / "scripts" / "install-ubuntu.sh"
        result = subprocess.run(
            [bash, self.bash_script_path(bash, installer), "--help"],
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
        self.assertIn("--auth-mode MODE", result.stdout)
        self.assertIn("--proxy-secret SECRET", result.stdout)
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
                command = [bash, self.bash_script_path(bash, installer), "--validate-paths-only", "--yes"]
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

    def test_ubuntu_installer_validates_trusted_proxy_config_before_privileged_file_operations(self):
        if os.name == "nt":
            self.skipTest("POSIX path validation requires POSIX paths")
        bash = self.bash_or_skip()
        installer = REPO_ROOT / "scripts" / "install-ubuntu.sh"

        with tempfile.TemporaryDirectory() as tempdir:
            base = Path(tempdir).resolve()
            command_base = [
                bash,
                self.bash_script_path(bash, installer),
                "--validate-paths-only",
                "--yes",
                "--install-dir",
                str(base / "opt" / "gatewatch"),
                "--data-dir",
                str(base / "var" / "lib" / "gatewatch"),
                "--env-dir",
                str(base / "etc" / "gatewatch"),
            ]

            missing_secret = subprocess.run(
                [*command_base, "--auth-mode", "trusted_proxy"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(missing_secret.returncode, 0)
            self.assertIn("--proxy-secret is required", missing_secret.stderr)

            weak_secret = subprocess.run(
                [*command_base, "--auth-mode", "trusted_proxy", "--proxy-secret", "short"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(weak_secret.returncode, 0)
            self.assertIn("--proxy-secret must be at least 16 characters", weak_secret.stderr)

            valid = subprocess.run(
                [
                    *command_base,
                    "--auth-mode",
                    "trusted-proxy",
                    "--proxy-secret",
                    "valid-proxy-secret-value",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertIn("Install path validation passed", valid.stdout)

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

        self.assertIn("FROM python:3.12-alpine", dockerfile)
        self.assertIn("GATEWATCH_DB=/data/gatewatch.db", dockerfile)
        self.assertIn("GATEWATCH_CONFIG_FILE=/data/gatewatch.env", dockerfile)
        self.assertIn("GATEWATCH_ALLOW_INSECURE_NETWORK=1", dockerfile)
        self.assertIn("rm -rf /usr/local/lib/python*/site-packages/pip*", dockerfile)
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

    def test_production_reverse_proxy_bundle_wires_trusted_proxy_auth(self):
        bundle = REPO_ROOT / "deploy" / "reverse-proxy"
        readme = (bundle / "README.md").read_text(encoding="utf-8")
        nginx = (bundle / "nginx-gatewatch.conf").read_text(encoding="utf-8")
        secret_snippet = (bundle / "nginx-gatewatch-proxy-secret.conf.example").read_text(encoding="utf-8")
        oauth_env = (bundle / "oauth2-proxy-gatewatch.env.example").read_text(encoding="utf-8")
        oauth_service = (bundle / "oauth2-proxy-gatewatch.service").read_text(encoding="utf-8")
        rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")
        root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("--auth-mode trusted_proxy", readme)
        self.assertIn("--proxy-secret", readme)
        self.assertIn("GATEWATCH_PROXY_SECRET", readme)
        self.assertIn("auth_request /oauth2/auth", nginx)
        self.assertIn("auth_request_set $auth_groups $upstream_http_x_auth_request_groups", nginx)
        self.assertIn("proxy_set_header X-Forwarded-User \"\"", nginx)
        self.assertIn("proxy_set_header X-Authenticated-Groups \"\"", nginx)
        self.assertIn("proxy_set_header X-Gatewatch-Proxy-Secret $gatewatch_proxy_secret", nginx)
        self.assertIn("proxy_set_header X-Remote-Groups $auth_groups", nginx)
        self.assertIn("REPLACE_WITH_GATEWATCH_PROXY_SECRET", secret_snippet)
        self.assertIn("OAUTH2_PROXY_PROVIDER=oidc", oauth_env)
        self.assertIn("OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/REPLACE_WITH_ENTRA_TENANT_ID/v2.0", oauth_env)
        self.assertIn("OAUTH2_PROXY_SET_XAUTHREQUEST=true", oauth_env)
        self.assertIn("OAUTH2_PROXY_PASS_BASIC_AUTH=false", oauth_env)
        self.assertIn("OAUTH2_PROXY_PASS_USER_HEADERS=false", oauth_env)
        self.assertIn("OAUTH2_PROXY_TRUSTED_PROXY_IPS=127.0.0.1/32,::1/128", oauth_env)
        self.assertIn("User=oauth2-proxy", oauth_service)
        self.assertIn("EnvironmentFile=/etc/oauth2-proxy/gatewatch.env", oauth_service)
        self.assertIn("Production Reverse Proxy Rollout", rollout)
        self.assertIn("deploy/reverse-proxy/README.md", root_readme)

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
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=15,
        )
        output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, output)

    def test_remote_container_deploy_validates_port_before_ssh(self):
        bash = self.bash_or_skip()
        script = REPO_ROOT / "scripts" / "deploy-container.sh"
        script_path = self.bash_script_path(bash, script)
        for port in ["0", "65536", "abc", "70000"]:
            with self.subTest(port=port):
                result = subprocess.run(
                    [bash, script_path, "--validate-only", "--target", "example.invalid", "--host-port", port],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--host-port must be a number from 1 to 65535", result.stderr)
                self.assertNotIn("Deploying Gatewatch", result.stdout)

        valid = subprocess.run(
            [bash, script_path, "--validate-only", "--target", "example.invalid", "--host-port", "65535"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertIn("Deploy configuration validation passed", valid.stdout)

        if self.bash_is_wsl(bash):
            command = (
                "GATEWATCH_HOST_PORT=70000 "
                f"{shlex.quote(script_path)} --validate-only --target example.invalid"
            )
            env_result = subprocess.run(
                [bash, "-lc", command],
                capture_output=True,
                text=True,
                timeout=15,
            )
        else:
            env = {**os.environ, "GATEWATCH_HOST_PORT": "70000"}
            env_result = subprocess.run(
                [bash, script_path, "--validate-only", "--target", "example.invalid"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
        self.assertNotEqual(env_result.returncode, 0)
        self.assertIn("--host-port must be a number from 1 to 65535", env_result.stderr)

    def test_mock_local_deployment_package_is_documented_and_reusable(self):
        package = REPO_ROOT / "deploy" / "mock-local"
        manifest = json.loads((package / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        readme = (package / "README.md").read_text(encoding="utf-8")
        helper = (package / "mock_deploy.py").read_text(encoding="utf-8")
        root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")

        self.assertEqual(
            manifest["default_source_url"],
            "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz",
        )
        self.assertIn("Mock Deployment Checklist", readme)
        self.assertIn("python deploy\\mock-local\\mock_deploy.py deploy --reset-data", readme)
        self.assertIn("python deploy\\mock-local\\mock_deploy.py teardown --verify-only", readme)
        self.assertIn("Invoke-RestMethod http://127.0.0.1:18087/healthz", readme)
        self.assertIn("mock container, image, and data volume", readme)
        self.assertIn("runtime_artifacts", (package / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIn("safe_extract", helper)
        self.assertIn("GATEWATCH_DB=/data/gatewatch.db", helper)
        self.assertIn("--read-only", helper)
        self.assertIn("no-new-privileges", helper)
        self.assertIn("remove_image", helper)
        self.assertIn("deploy/mock-local", root_readme)
        self.assertIn("Local Mock Deployment", rollout)

    def test_mock_local_package_inspection_runs_without_docker(self):
        helper = REPO_ROOT / "deploy" / "mock-local" / "mock_deploy.py"
        result = subprocess.run(
            [sys.executable, str(helper), "inspect-package"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Package inspection passed: deploy", result.stdout)


if __name__ == "__main__":
    unittest.main()
