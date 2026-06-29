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

        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG", compose)
        self.assertIn("/data/audit-events.jsonl", compose)
        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED", compose)
        self.assertIn("ACCESS_REGISTER_AUDIT_EVENT_LOG=/data/audit-events.jsonl", env_example)


if __name__ == "__main__":
    unittest.main()
