import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV_MARKERS = ("SECRET", "PASSWORD", "TOKEN", "PRIVATE_KEY", "API_KEY", "ACCESS_KEY", "CREDENTIAL")
ALLOWED_DOCKERFILE_ENV_NAMES = {"ACCESS_REGISTER_AUTH_MODE"}


def dockerfile_env_names(dockerfile: str) -> list[str]:
    names = []
    in_env_block = False
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ENV "):
            in_env_block = True
            line = line.removeprefix("ENV ").strip()
        elif not in_env_block:
            continue

        continued = line.endswith("\\")
        line = line.rstrip("\\").strip()
        names.extend(token.split("=", 1)[0] for token in line.split() if "=" in token)
        in_env_block = continued
    return names


class DeploymentFileTests(unittest.TestCase):
    def test_dockerfile_keeps_trusted_proxy_default_without_secret_values(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertTrue(dockerfile.startswith("# check=skip=SecretsUsedInArgOrEnv\n"))
        self.assertIn("ACCESS_REGISTER_AUTH_MODE=trusted_proxy", dockerfile)
        self.assertNotIn("ACCESS_REGISTER_PROXY_SECRET=", dockerfile)

    def test_dockerfile_secret_check_suppression_has_env_name_guardrail(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        env_names = dockerfile_env_names(dockerfile)
        secret_like_names = [
            name
            for name in env_names
            if name not in ALLOWED_DOCKERFILE_ENV_NAMES
            and any(marker in name.upper() for marker in SECRET_ENV_MARKERS)
        ]

        self.assertIn("ACCESS_REGISTER_AUTH_MODE", env_names)
        self.assertEqual(secret_like_names, [])

    def test_container_health_checks_use_non_sensitive_health_endpoint(self):
        deployment_files = [
            REPO_ROOT / "Dockerfile",
            REPO_ROOT / "docker" / "vsphere" / "compose.yaml",
            REPO_ROOT / "docker" / "full-test" / "compose.yaml",
        ]

        for path in deployment_files:
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertIn("/healthz", text)
                self.assertNotIn("/api/summary", text)

    def test_vsphere_profile_exports_audit_events_for_log_shipping(self):
        compose = (REPO_ROOT / "docker" / "vsphere" / "compose.yaml").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / "docker" / "vsphere" / ".env.example").read_text(encoding="utf-8")
        runbook = (REPO_ROOT / "docs" / "vsphere-technician-runbook.md").read_text(encoding="utf-8")

        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG", compose)
        self.assertIn("/data/audit-events.jsonl", compose)
        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED", compose)
        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG=/data/audit-events.jsonl", env_example)
        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG", runbook)
        self.assertIn("Get-Content $AuditEventPath -Tail 3", runbook)

    def test_production_installer_prompts_for_site_specific_values(self):
        script = (REPO_ROOT / "scripts" / "install-gatewatch-production.ps1").read_text(encoding="utf-8")
        launcher = (REPO_ROOT / "Deploy-Gatewatch.ps1").read_text(encoding="utf-8")
        launcher_cmd = (REPO_ROOT / "Deploy-Gatewatch.cmd").read_text(encoding="utf-8")
        checklist = (REPO_ROOT / "docs" / "production-checklist.md").read_text(encoding="utf-8")
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("Deploy-Gatewatch.cmd", checklist)
        self.assertIn("Deploy-Gatewatch.ps1", launcher_cmd)
        self.assertIn("Start-Process", launcher)
        self.assertIn("-Verb RunAs", launcher)
        self.assertIn("Copy-SourceToInstallRoot", launcher)
        self.assertIn("-SkipGitFetch", launcher)
        self.assertIn("if ($InstallerArguments.Count -gt 0)", launcher)
        self.assertIn("scripts\\install-gatewatch-production.ps1", launcher)
        self.assertIn("Read-Host", script)
        self.assertIn("Where to get it", script)
        self.assertIn("https://github.com/skellywix/eric-gatewatch.git", script)
        self.assertIn("[switch]$PrivateGitHubRepo", script)
        self.assertIn("ssh-keygen", script)
        self.assertIn("GitHub deploy key required", script)
        self.assertIn("Private repo mode is enabled", script)
        self.assertIn("Sync-GitRepository", script)
        self.assertIn("Ensure-DockerRuntime", script)
        self.assertIn("Installer downloads must use HTTPS", script)
        self.assertIn("SkipAdSyncTaskPrompt", script)
        self.assertIn("X-Access-Register-Proxy-Secret", script)
        self.assertIn("ACCESS_REGISTER_PROXY_SECRET", script)
        self.assertIn('Invoke-DockerCompose -ComposeArguments @("config", "--quiet")', script)
        self.assertIn("Invoke-DockerCompose -ComposeArguments @(\"up\", \"-d\", \"--build\")", script)
        self.assertIn("Wait-GatewatchHealth", script)
        self.assertIn("deployment-handoff.txt", script)
        self.assertNotIn("ACCESS_REGISTER_PROXY_SECRET=replace-with", script)
        self.assertIn("scripts\\install-gatewatch-production.ps1", checklist)
        self.assertIn("Each prompt tells you where to get the value", checklist)
        self.assertIn("public and does not need a deploy key", checklist)
        self.assertIn("read-only deploy key", checklist)
        self.assertIn("https://github.com/skellywix/eric-gatewatch/settings/keys", checklist)
        self.assertIn("config --quiet", checklist)
        self.assertIn("docker/vsphere/deployment-handoff.txt", gitignore)
        self.assertIn("docker/vsphere/gatewatch-ad-sync-task.local.ps1", gitignore)


if __name__ == "__main__":
    unittest.main()
