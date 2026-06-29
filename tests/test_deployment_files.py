import os
import base64
import json
import shutil
import subprocess
import tempfile
import textwrap
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


def ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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
        repair = (REPO_ROOT / "scripts" / "repair-gatewatch-deployment.ps1").read_text(encoding="utf-8")
        launcher = (REPO_ROOT / "Deploy-Gatewatch.ps1").read_text(encoding="utf-8")
        launcher_cmd = (REPO_ROOT / "Deploy-Gatewatch.cmd").read_text(encoding="utf-8")
        checklist = (REPO_ROOT / "docs" / "production-checklist.md").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker" / "vsphere" / "compose.yaml").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / "docker" / "vsphere" / ".env.example").read_text(encoding="utf-8")
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("Deploy-Gatewatch.cmd", checklist)
        self.assertIn("Deploy-Gatewatch.ps1", launcher_cmd)
        self.assertIn("Start-Process", launcher)
        self.assertIn("-Verb RunAs", launcher)
        self.assertIn("Copy-SourceToInstallRoot", launcher)
        self.assertIn("-SkipGitFetch", launcher)
        self.assertIn("SkipSelfUpdate", launcher)
        self.assertIn("SourceArchiveUrl", launcher)
        self.assertIn("InstallerArgumentsJson", launcher)
        self.assertIn("InstallerArgumentsBase64", launcher)
        self.assertIn("Convert-ArgumentsToBase64", launcher)
        self.assertIn("Sync-DownloadedSourceFromGitHub", launcher)
        self.assertIn("Refresh downloaded files from public GitHub", launcher)
        self.assertIn("Gatewatch/archive/refs/heads/main.zip", launcher)
        self.assertIn("Gatewatch source archive URL", launcher)
        self.assertIn("must use HTTPS", launcher)
        self.assertIn("rerun with -SkipSelfUpdate only if this folder already contains the approved release", launcher)
        self.assertIn("if ($script:EffectiveInstallerArguments.Count -gt 0)", launcher)
        self.assertIn("Get-EffectiveInstallerArguments", launcher)
        self.assertIn("$installerExitCode = $installerProcess.ExitCode", launcher)
        self.assertIn("$global:LASTEXITCODE = 0", launcher)
        self.assertIn("scripts\\install-gatewatch-production.ps1", launcher)
        self.assertIn("repair-gatewatch-deployment.ps1", checklist)
        self.assertIn("old broken download", checklist)
        self.assertIn("raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/repair-gatewatch-deployment.ps1", checklist)
        self.assertIn("Downloads a fresh public Gatewatch copy", repair)
        self.assertIn("Assert-HttpsUrl", repair)
        self.assertIn("ArchiveUrl", repair)
        self.assertIn("InstallerArgumentsJson", repair)
        self.assertIn("InstallerArgumentsBase64", repair)
        self.assertIn("Convert-ArgumentsToJson", repair)
        self.assertIn("Convert-ArgumentsToBase64", repair)
        self.assertIn("SkipDeploy", repair)
        self.assertIn("Deploy-Gatewatch.ps1", repair)
        self.assertIn("Existing Docker volumes and env files are not deleted", repair)
        self.assertIn("Read-Host", script)
        self.assertIn("Where to get it", script)
        self.assertIn("InstallerBoundParameters", script)
        self.assertIn('Test-InstallerParameter "BindAddress"', script)
        self.assertNotIn("$PSBoundParameters.ContainsKey", script)
        self.assertIn("https://github.com/skellywix/Gatewatch.git", script)
        self.assertIn('[ValidateSet("auto", "trusted_proxy", "local")]', script)
        self.assertIn("Test-LocalGatewatchUrl", script)
        self.assertIn("Using local role-selector auth for a loopback laptop test URL", script)
        self.assertIn("ACCESS_REGISTER_AUTH_MODE=local is allowed only when GATEWATCH_BIND_ADDRESS is loopback", script)
        self.assertIn("[switch]$PrivateGitHubRepo", script)
        self.assertIn("[string]$GitInstaller", script)
        self.assertIn("Resolve-GitForWindowsInstaller", script)
        self.assertIn("https://api.github.com/repos/git-for-windows/git/releases/latest", script)
        self.assertIn("Add-WindowsCapability -Online -Name OpenSSH.Client", script)
        self.assertIn("ssh-keygen", script)
        self.assertIn("GitHub deploy key required", script)
        self.assertIn("Private repo mode is enabled", script)
        self.assertIn("Sync-GitRepository", script)
        self.assertIn("Quote-ProcessArgument", script)
        self.assertIn("Start-Process -FilePath $FilePath", script)
        self.assertIn("$exitCode = $process.ExitCode", script)
        self.assertIn("Exit code: $exitCode", script)
        self.assertIn("$global:LASTEXITCODE = 0", script)
        self.assertIn("Ensure-DockerRuntime", script)
        self.assertIn("DockerDesktopInstallerUrl", script)
        self.assertIn("install --quiet --accept-license --backend=wsl-2 --always-run-service", script)
        self.assertIn("Docker Desktop is not supported on Windows Server", script)
        self.assertIn("Test-DockerDaemon", script)
        self.assertIn("Test-DockerRuntime", script)
        self.assertIn("Start-DockerRuntime", script)
        self.assertIn("Wait-DockerRuntime", script)
        self.assertIn("Docker CLI and Compose plugin are installed, but the Docker engine is not responding", script)
        self.assertIn("@(\"--install\", \"--no-distribution\")", script)
        self.assertIn("Installer downloads must use HTTPS", script)
        self.assertIn("SkipAdSyncTaskPrompt", script)
        self.assertIn("X-Access-Register-Proxy-Secret", script)
        self.assertIn("ACCESS_REGISTER_PROXY_SECRET", script)
        self.assertIn("[Security.Cryptography.RandomNumberGenerator]::Create()", script)
        self.assertIn("$rng.GetBytes($bytes)", script)
        self.assertNotIn("RandomNumberGenerator]::Fill", script)
        self.assertIn('Invoke-DockerCompose -ComposeArguments @("config", "--quiet")', script)
        self.assertIn("Invoke-DockerCompose -ComposeArguments @(\"up\", \"-d\", \"--build\")", script)
        self.assertIn("Wait-GatewatchHealth", script)
        self.assertIn("deployment-handoff.txt", script)
        self.assertNotIn("ACCESS_REGISTER_PROXY_SECRET=replace-with", script)
        self.assertIn('ACCESS_REGISTER_AUTH_MODE: "${ACCESS_REGISTER_AUTH_MODE:-trusted_proxy}"', compose)
        self.assertIn('ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK: "${ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK:-0}"', compose)
        self.assertIn("ACCESS_REGISTER_AUTH_MODE=trusted_proxy", env_example)
        self.assertIn("ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=0", env_example)
        self.assertIn("scripts\\install-gatewatch-production.ps1", checklist)
        self.assertIn("laptop proof test", checklist)
        self.assertIn("http://localhost:8087", checklist)
        self.assertIn("Each prompt tells you where to get the value", checklist)
        self.assertIn("fully automatic dependency bootstrap", checklist)
        self.assertIn("Windows 10/11 Pro or Enterprise", checklist)
        self.assertIn("Docker Desktop is not supported on Windows Server", checklist)
        self.assertIn("Git for Windows releases", checklist)
        self.assertIn("Windows OpenSSH install", checklist)
        self.assertIn("public and does not need a deploy key", checklist)
        self.assertIn("read-only deploy key", checklist)
        self.assertIn("https://github.com/skellywix/Gatewatch/settings/keys", checklist)
        self.assertIn("config --quiet", checklist)
        self.assertIn("docker/vsphere/deployment-handoff.txt", gitignore)
        self.assertIn("docker/vsphere/gatewatch-ad-sync-task.local.ps1", gitignore)

    def test_installer_waits_for_docker_daemon_when_compose_plugin_exists(self):
        if os.name != "nt":
            self.skipTest("PowerShell deployment bootstrap is Windows-specific")
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        with tempfile.TemporaryDirectory(prefix="gatewatch-fake-docker-") as temp_dir:
            temp_root = Path(temp_dir)
            app_root = temp_root / "app"
            install_root = temp_root / "install"
            fake_bin = temp_root / "fake-bin"
            fake_program_files = temp_root / "ProgramFiles"
            fake_bin.mkdir()
            fake_program_files.mkdir()
            (app_root / "docker" / "vsphere").mkdir(parents=True)

            for relative_path in (
                "app.py",
                "Dockerfile",
                "docker/vsphere/compose.yaml",
                "docker/vsphere/.env.example",
            ):
                source = REPO_ROOT / relative_path
                target = app_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            fake_docker = fake_bin / "fake-docker.ps1"
            fake_docker.write_text(
                textwrap.dedent(
                    r'''
                    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DockerArgs)

                    $statePath = Join-Path $PSScriptRoot "docker-version-count.txt"
                    $logPath = Join-Path $PSScriptRoot "docker.log"
                    Add-Content -Path $logPath -Value ($DockerArgs -join " ")

                    if ($DockerArgs.Count -ge 2 -and $DockerArgs[0] -eq "compose" -and $DockerArgs[1] -eq "version") {
                        Write-Output "Docker Compose version v5.fake"
                        exit 0
                    }

                    if ($DockerArgs.Count -ge 1 -and $DockerArgs[0] -eq "version") {
                        $count = 0
                        if (Test-Path -LiteralPath $statePath) {
                            $count = [int](Get-Content -LiteralPath $statePath)
                        }
                        $count += 1
                        Set-Content -LiteralPath $statePath -Value ([string]$count)
                        if ($count -lt 3) {
                            Write-Error "Cannot connect to the Docker daemon"
                            exit 1
                        }
                        if ($DockerArgs -contains "--format") {
                            Write-Output "29.5.3"
                        } else {
                            Write-Output "Client: fake"
                            Write-Output "Server: fake"
                        }
                        exit 0
                    }

                    if ($DockerArgs.Count -ge 1 -and $DockerArgs[0] -eq "compose") {
                        exit 0
                    }

                    exit 0
                    '''
                ).strip(),
                encoding="utf-8",
            )
            (fake_bin / "docker.cmd").write_text(
                '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-docker.ps1" %*\r\n',
                encoding="ascii",
            )

            runner = temp_root / "run-installer.ps1"
            runner.write_text(
                textwrap.dedent(
                    f"""
                    $ErrorActionPreference = "Stop"
                    function Get-Service {{
                        param([string[]]$Name, $ErrorAction)
                        return $null
                    }}
                    $env:ProgramFiles = {ps_quote(fake_program_files)}
                    $env:PATH = {ps_quote(fake_bin)} + ";" + $env:PATH
                    & {ps_quote(REPO_ROOT / "scripts" / "install-gatewatch-production.ps1")} `
                        -InstallRoot {ps_quote(install_root)} `
                        -AppRoot {ps_quote(app_root)} `
                        -SkipGitFetch `
                        -GatewatchUrl "http://localhost:8087" `
                        -AdminGroups "TEST\\Gatewatch-Admins" `
                        -ProxySecret "test-proxy-secret" `
                        -BindAddress "127.0.0.1" `
                        -Scheduler "0" `
                        -SkipStart `
                        -SkipHealthCheck `
                        -SkipEnvAclHardening `
                        -SkipAdSyncTaskPrompt
                    """
                ).strip(),
                encoding="utf-8",
            )

            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                capture_output=True,
                text=True,
                timeout=45,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "Docker CLI and Compose plugin are installed, but the Docker engine is not responding",
                result.stdout,
            )
            self.assertNotIn("Will the reverse proxy run on this same VM?", result.stdout)
            version_attempts = int((fake_bin / "docker-version-count.txt").read_text(encoding="utf-8"))
            self.assertGreaterEqual(version_attempts, 3)
            self.assertTrue((app_root / "docker" / "vsphere" / ".env").is_file())
            self.assertTrue((app_root / "docker" / "vsphere" / "deployment-handoff.txt").is_file())
            env_text = (app_root / "docker" / "vsphere" / ".env").read_text(encoding="ascii")
            handoff_text = (app_root / "docker" / "vsphere" / "deployment-handoff.txt").read_text(encoding="ascii")
            self.assertIn("ACCESS_REGISTER_AUTH_MODE=local", env_text)
            self.assertIn("ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=1", env_text)
            self.assertIn("Auth mode: local", handoff_text)

    def test_powershell_deployment_scripts_parse(self):
        if os.name != "nt":
            self.skipTest("PowerShell deployment bootstrap is Windows-specific")
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        script_paths = [
            REPO_ROOT / "Deploy-Gatewatch.ps1",
            REPO_ROOT / "scripts" / "install-gatewatch-production.ps1",
            REPO_ROOT / "scripts" / "repair-gatewatch-deployment.ps1",
        ]
        ps_paths = ", ".join(ps_quote(path) for path in script_paths)
        command = textwrap.dedent(
            f"""
            $ErrorActionPreference = "Stop"
            foreach ($path in @({ps_paths})) {{
                [scriptblock]::Create((Get-Content -LiteralPath $path -Raw)) | Out-Null
            }}
            """
        ).strip()

        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repair_script_rejects_non_https_archive_url(self):
        if os.name != "nt":
            self.skipTest("PowerShell deployment bootstrap is Windows-specific")
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        with tempfile.TemporaryDirectory(prefix="gatewatch-repair-https-") as temp_dir:
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(REPO_ROOT / "scripts" / "repair-gatewatch-deployment.ps1"),
                    "-NoElevate",
                    "-SkipDeploy",
                    "-DestinationRoot",
                    temp_dir,
                    "-ArchiveUrl",
                    "http://example.invalid/Gatewatch.zip",
                    "-InstallerArgumentsBase64",
                    base64.b64encode(
                        json.dumps(
                            [
                                "-GatewatchUrl",
                                "http://localhost:8087",
                                "-AdminGroups",
                                "TEST\\Gatewatch-Admins",
                            ]
                        ).encode("utf-8")
                    ).decode("ascii"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Gatewatch source archive URL must use HTTPS", result.stdout + result.stderr)
        self.assertNotIn("parameter name 'GatewatchUrl'", result.stdout + result.stderr)

    def test_installer_re_resolves_auth_mode_on_rerun_from_existing_env(self):
        if os.name != "nt":
            self.skipTest("PowerShell deployment bootstrap is Windows-specific")
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        with tempfile.TemporaryDirectory(prefix="gatewatch-rerun-auth-") as temp_dir:
            temp_root = Path(temp_dir)
            app_root = temp_root / "app"
            install_root = temp_root / "install"
            fake_bin = temp_root / "fake-bin"
            fake_program_files = temp_root / "ProgramFiles"
            fake_bin.mkdir()
            fake_program_files.mkdir()
            (app_root / "docker" / "vsphere").mkdir(parents=True)

            for relative_path in (
                "app.py",
                "Dockerfile",
                "docker/vsphere/compose.yaml",
                "docker/vsphere/.env.example",
            ):
                source = REPO_ROOT / relative_path
                target = app_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            env_path = app_root / "docker" / "vsphere" / ".env"
            env_path.write_text(
                "\n".join(
                    (
                        "GATEWATCH_IMAGE=gatewatch:stale",
                        "GATEWATCH_CONTAINER_NAME=gatewatch-stale",
                        "GATEWATCH_DATA_VOLUME=gatewatch-stale-data",
                        "GATEWATCH_NETWORK=gatewatch-stale-net",
                        "GATEWATCH_BIND_ADDRESS=127.0.0.1",
                        "GATEWATCH_APP_PORT=8087",
                        "ACCESS_REGISTER_SCHEDULER=0",
                        "ACCESS_REGISTER_AUTH_MODE=trusted_proxy",
                        "ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=0",
                        "ACCESS_REGISTER_PROXY_SECRET=stale-secret",
                        "ACCESS_REGISTER_AUDIT_EVENT_LOG=/data/audit-events.jsonl",
                        "ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED=0",
                        "ACCESS_REGISTER_ADMIN_GROUPS=TEST\\Gatewatch-Admins",
                    )
                ),
                encoding="ascii",
            )

            fake_docker = fake_bin / "fake-docker.ps1"
            fake_docker.write_text(
                textwrap.dedent(
                    r'''
                    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DockerArgs)
                    if ($DockerArgs.Count -ge 2 -and $DockerArgs[0] -eq "compose" -and $DockerArgs[1] -eq "version") {
                        Write-Output "Docker Compose version v5.fake"
                        exit 0
                    }
                    if ($DockerArgs.Count -ge 1 -and $DockerArgs[0] -eq "version") {
                        if ($DockerArgs -contains "--format") {
                            Write-Output "29.5.3"
                        } else {
                            Write-Output "Client: fake"
                            Write-Output "Server: fake"
                        }
                        exit 0
                    }
                    if ($DockerArgs.Count -ge 1 -and $DockerArgs[0] -eq "compose") {
                        exit 0
                    }
                    exit 0
                    '''
                ).strip(),
                encoding="utf-8",
            )
            (fake_bin / "docker.cmd").write_text(
                '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-docker.ps1" %*\r\n',
                encoding="ascii",
            )

            runner = temp_root / "run-installer.ps1"
            runner.write_text(
                textwrap.dedent(
                    f"""
                    $ErrorActionPreference = "Stop"
                    $env:ProgramFiles = {ps_quote(fake_program_files)}
                    $env:PATH = {ps_quote(fake_bin)} + ";" + $env:PATH
                    & {ps_quote(REPO_ROOT / "scripts" / "install-gatewatch-production.ps1")} `
                        -InstallRoot {ps_quote(install_root)} `
                        -AppRoot {ps_quote(app_root)} `
                        -SkipGitFetch `
                        -GatewatchUrl "http://localhost:8087" `
                        -AdminGroups "TEST\\Gatewatch-Admins" `
                        -ProxySecret "test-proxy-secret" `
                        -BindAddress "127.0.0.1" `
                        -Scheduler "0" `
                        -SkipStart `
                        -SkipHealthCheck `
                        -SkipEnvAclHardening `
                        -SkipAdSyncTaskPrompt
                    """
                ).strip(),
                encoding="utf-8",
            )

            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                capture_output=True,
                text=True,
                timeout=45,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            env_text = env_path.read_text(encoding="ascii")
            handoff_text = (app_root / "docker" / "vsphere" / "deployment-handoff.txt").read_text(encoding="ascii")
            self.assertIn("Using local role-selector auth for a loopback laptop test URL", result.stdout)
            self.assertIn("ACCESS_REGISTER_AUTH_MODE=local", env_text)
            self.assertIn("ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=1", env_text)
            self.assertIn("Auth mode: local", handoff_text)


if __name__ == "__main__":
    unittest.main()
