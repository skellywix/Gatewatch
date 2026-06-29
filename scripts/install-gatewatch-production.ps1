<#
.SYNOPSIS
Fetches Gatewatch from GitHub and automates the VM-local Docker production setup from a Windows VM desktop.

.DESCRIPTION
Copy this script to the VM and run it from any folder. It installs or verifies
Git, OpenSSH when private GitHub deploy-key mode is used, Docker, and Docker
Compose; fetches the Gatewatch repo from GitHub; prompts for site-specific
production values; writes docker/vsphere/.env; starts the Docker Compose
profile; checks /healthz; optionally registers the AD sync scheduled task; and
writes a non-secret handoff file.

For a fully automatic dependency bootstrap, use a supported Windows 10/11 Pro or
Enterprise VM so Docker Desktop can be installed from Docker's official command
line installer. Docker Desktop is not supported on Windows Server; on Windows
Server, install an approved Linux-container runtime first or pass a site-approved
runtime installer with -DockerInstaller.

The default repository URL is public HTTPS, so a deploy key is not needed for
normal installs. If the repo is private again later, run with
`-PrivateGitHubRepo`; the script will generate an Ed25519 deploy key and tell
you exactly where to add the public key in GitHub before it retries the clone.

It cannot create your DNS record, TLS certificate, AD SSO reverse proxy, or
enterprise backup policy because those live in site infrastructure. It collects
the needed values and records those next steps in the handoff file.

Main docs:
- docs/production-checklist.md
- docs/on-prem-docker-ad-sso.md
- docker/vsphere/README.md

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-gatewatch-production.ps1

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-gatewatch-production.ps1 `
  -GatewatchUrl "https://gatewatch.company.local" `
  -AdminGroups "COMPANY\Gatewatch-Admins" `
  -RunVerification
#>

[CmdletBinding()]
param(
    [string]$InstallRoot,
    [string]$AppRoot,
    [string]$EnvPath,
    [string]$GitRepoUrl = "https://github.com/skellywix/Gatewatch.git",
    [string]$GitBranch = "main",
    [switch]$PrivateGitHubRepo,
    [switch]$UseExistingGitAuth,
    [switch]$SkipGitFetch,
    [string]$DeployKeyPath,
    [string]$GitHubDeployKeysUrl = "https://github.com/skellywix/Gatewatch/settings/keys",
    [switch]$SkipDependencyInstall,
    [string]$GitInstaller,
    [string]$GitInstallerArguments = "/VERYSILENT /NORESTART /NOCANCEL /SP-",
    [string]$DockerInstaller,
    [string]$DockerInstallerArguments,
    [switch]$DisableDockerDesktopAutoInstall,
    [string]$DockerDesktopInstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe",
    [string]$DockerDesktopInstallerArguments = "install --quiet --accept-license --backend=wsl-2 --always-run-service",
    [switch]$SkipWslSetup,
    [string]$Image = "gatewatch:vsphere",
    [string]$ContainerName = "gatewatch-app",
    [string]$DataVolume = "gatewatch-data",
    [string]$NetworkName = "gatewatch-internal",
    [string]$BindAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$AppPort = 8087,
    [string]$AdminGroups = "DOMAIN\AccessRegister-Admins",
    [string]$SupervisorGroups = "DOMAIN\AccessRegister-Supervisors",
    [string]$ReviewerGroups = "DOMAIN\AccessRegister-Reviewers",
    [string]$HrGroups = "DOMAIN\AccessRegister-HR",
    [string]$ReadOnlyGroups = "DOMAIN\AccessRegister-ReadOnly",
    [string]$ProxySecret,
    [string]$AuditEventLog = "/data/audit-events.jsonl",
    [ValidateSet("0", "1")]
    [string]$AuditEventLogRequired = "0",
    [ValidateSet("0", "1")]
    [string]$Scheduler = "1",
    [switch]$ForceEnv,
    [switch]$RunVerification,
    [switch]$SkipBuild,
    [switch]$SkipStart,
    [switch]$SkipHealthCheck,
    [switch]$SkipEnvAclHardening,
    [string]$AllowedProxyRemoteAddress,
    [switch]$SkipFirewallRule,
    [string]$FirewallRuleName = "Gatewatch App Port for Reverse Proxy",
    [string]$GatewatchUrl = "https://gatewatch.company.local",
    [switch]$RegisterAdSyncTask,
    [switch]$SkipAdSyncTaskPrompt,
    [string]$AdSyncTaskName = "Gatewatch AD Sync",
    [string]$AdSyncServiceAccount,
    [string]$AdSyncSearchBase,
    [string]$AdSyncStartTime = "02:30",
    [switch]$AdSyncDirectLocal,
    [string]$AdSyncRemoteUser = "DOMAIN\svc-gatewatch-adsync",
    [string]$AdSyncRemoteGroups,
    [switch]$AdSyncRouteDisabledAccess,
    [string]$HandoffPath
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Write-Info {
    param([string]$Message)
    Write-Host "[info] $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
}

function Get-DefaultInstallRoot {
    if (Test-Path -LiteralPath "D:\" -PathType Container) {
        return "D:\AccessRegister"
    }
    return "C:\AccessRegister"
}

function Resolve-FullPath {
    param([string]$Path)
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Assert-FileExists {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description was not found at $Path"
    }
}

function Assert-DirectoryExists {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Description was not found at $Path"
    }
}

function Assert-SafeEnvValue {
    param(
        [string]$Name,
        [AllowNull()]
        [string]$Value
    )
    if ($null -eq $Value) {
        return
    }
    if ($Value.Contains("`r") -or $Value.Contains("`n") -or $Value.Contains([char]0)) {
        throw "$Name contains a newline or null character and cannot be written to an env file."
    }
}

function Test-LoopbackBind {
    param([string]$Address)
    return $Address -in @("127.0.0.1", "localhost", "::1")
}

function Test-PlaceholderSecret {
    param([string]$Value)
    if (-not $Value) {
        return $true
    }
    return $Value -match "replace-with|change-me|placeholder|example"
}

function Test-ExampleValue {
    param([string]$Value)
    if (-not $Value) {
        return $true
    }
    return $Value -match "DOMAIN\\|company\.local|example|placeholder|replace-with|change-me"
}

function Convert-SecureStringToPlainText {
    param([securestring]$Value)
    if (-not $Value) {
        return ""
    }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

function Read-TextInput {
    param(
        [string]$Title,
        [string]$Help,
        [AllowNull()]
        [string]$DefaultValue,
        [switch]$Required
    )

    Write-Host ""
    Write-Host $Title
    if ($Help) {
        Write-Host "  Where to get it: $Help"
    }
    if ($DefaultValue) {
        Write-Host "  Press Enter to use: $DefaultValue"
    }

    while ($true) {
        $inputValue = Read-Host "  Value"
        if (-not [string]::IsNullOrWhiteSpace($inputValue)) {
            return $inputValue.Trim()
        }
        if ($DefaultValue) {
            return $DefaultValue
        }
        if (-not $Required) {
            return ""
        }
        Write-Warn "This value is required."
    }
}

function Read-YesNo {
    param(
        [string]$Title,
        [string]$Help,
        [bool]$DefaultYes = $true
    )

    $suffix = if ($DefaultYes) { "Y/n" } else { "y/N" }
    Write-Host ""
    Write-Host $Title
    if ($Help) {
        Write-Host "  $Help"
    }
    while ($true) {
        $answer = Read-Host "  Choose [$suffix]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $DefaultYes
        }
        switch -Regex ($answer.Trim()) {
            "^(y|yes)$" { return $true }
            "^(n|no)$" { return $false }
            default { Write-Warn "Enter yes or no." }
        }
    }
}

function Install-WingetPackage {
    param(
        [string]$PackageId,
        [string]$Name,
        [string]$HelpUrl
    )

    if ($SkipDependencyInstall) {
        throw "$Name is required but was not found. Install it from $HelpUrl, then rerun this script."
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found, and winget is not available."
    }

    Write-Info "Installing $Name using winget package '$PackageId'."
    Invoke-External -FilePath "winget" -Arguments @(
        "install",
        "--id",
        $PackageId,
        "--exact",
        "--accept-package-agreements",
        "--accept-source-agreements"
    ) -FailureMessage "$Name installation failed."
}

function Resolve-GitForWindowsInstaller {
    if ($GitInstaller) {
        return Resolve-Installer -Installer $GitInstaller -DownloadName "GitForWindowsInstaller.exe"
    }

    $releaseUrl = "https://api.github.com/repos/git-for-windows/git/releases/latest"
    Write-Info "Finding the latest Git for Windows installer from $releaseUrl"
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers @{ "User-Agent" = "Gatewatch-Production-Installer" }
    $asset = $release.assets |
        Where-Object { $_.name -match "64-bit\.exe$" -and $_.name -notmatch "portable|busybox|min" } |
        Select-Object -First 1
    if (-not $asset) {
        throw "Could not find a Git for Windows 64-bit installer asset in the latest release."
    }

    return Resolve-Installer -Installer $asset.browser_download_url -DownloadName $asset.name
}

function Install-GitForWindows {
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Install-WingetPackage `
                -PackageId "Git.Git" `
                -Name "Git for Windows" `
                -HelpUrl "https://git-scm.com/download/win"
            return
        }
    } catch {
        Write-Warn "Winget Git install did not complete: $($_.Exception.Message)"
    }

    $installerPath = Resolve-GitForWindowsInstaller
    Invoke-Installer -InstallerPath $installerPath -Arguments $GitInstallerArguments -Name "Git for Windows"
}

function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        return
    }
    if ($SkipDependencyInstall) {
        throw "Git for Windows is required but was not found. Install it from https://git-scm.com/download/win, then rerun this script."
    }
    Install-GitForWindows
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git still was not found on PATH. Open a new elevated PowerShell session or add Git to PATH, then rerun this script."
    }
}

function Ensure-OpenSshClient {
    if (Get-Command ssh-keygen -ErrorAction SilentlyContinue) {
        return
    }
    if ($SkipDependencyInstall) {
        throw "OpenSSH Client is required to generate a GitHub deploy key. Install Windows OpenSSH Client, then rerun this script."
    }

    Write-Info "Installing Windows OpenSSH Client capability for GitHub deploy-key setup."
    Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0 | Out-Null
    if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
        throw "OpenSSH Client installation finished, but ssh-keygen is not on PATH. Open a new elevated PowerShell session and rerun this script."
    }
}

function Test-DockerCompose {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return $false
    }
    & docker compose version *> $null
    return $LASTEXITCODE -eq 0
}

function Get-WindowsProductType {
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        return [int]$os.ProductType
    } catch {
        return 0
    }
}

function Test-WindowsServer {
    $productType = Get-WindowsProductType
    return $productType -in @(2, 3)
}

function Test-DockerDesktopSupportedHost {
    $productType = Get-WindowsProductType
    return $productType -eq 1
}

function Wait-DockerCompose {
    param(
        [int]$Attempts = 45,
        [int]$DelaySeconds = 4
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if (Test-DockerCompose) {
            return
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    throw "Docker was installed or launched, but 'docker compose version' still failed. Start Docker or open a new elevated PowerShell session, then rerun this script."
}

function Resolve-Installer {
    param(
        [string]$Installer,
        [string]$DownloadName
    )
    if (-not $Installer) {
        return ""
    }
    if ($Installer -match "^http://") {
        throw "Installer downloads must use HTTPS. Use an approved local installer path or an https:// URL."
    }
    if ($Installer -match "^https://") {
        $downloadPath = Join-Path $env:TEMP $DownloadName
        Write-Info "Downloading installer from $Installer"
        Invoke-WebRequest -Uri $Installer -OutFile $downloadPath
        return $downloadPath
    }
    return (Resolve-FullPath $Installer)
}

function Ensure-WslForDockerDesktop {
    if ($SkipWslSetup) {
        Write-Info "Skipping WSL setup."
        return
    }
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        Write-Warn "WSL command was not found. Docker Desktop may install it, or the VM may need a reboot after optional Windows features are enabled."
        return
    }

    & wsl --status *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Step "Install Windows WSL support for Docker Desktop"
    Invoke-External -FilePath "wsl" -Arguments @("--install", "--no-distribution") -FailureMessage "WSL setup failed."
    Write-Warn "WSL setup can require a reboot. If Docker does not start after install, reboot the VM and rerun Deploy-Gatewatch.cmd."
}

function Invoke-Installer {
    param(
        [string]$InstallerPath,
        [string]$Arguments,
        [string]$Name
    )
    Assert-FileExists -Path $InstallerPath -Description "$Name installer"
    if ([IO.Path]::GetExtension($InstallerPath) -ieq ".msi") {
        $msiArguments = "/i `"$InstallerPath`" $Arguments"
        Write-Host "> msiexec.exe $msiArguments"
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArguments -Wait -PassThru
    } else {
        Write-Host "> $InstallerPath $Arguments"
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $Arguments -Wait -PassThru
    }
    if ($process.ExitCode -ne 0) {
        throw "$Name installer failed. Exit code: $($process.ExitCode)"
    }
}

function Start-DockerDesktop {
    $desktopPath = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path -LiteralPath $desktopPath -PathType Leaf) {
        Write-Info "Starting Docker Desktop."
        Start-Process -FilePath $desktopPath | Out-Null
    }
}

function Install-DockerDesktopRuntime {
    if ($DisableDockerDesktopAutoInstall) {
        throw "Docker with the Compose plugin is required, and Docker Desktop auto-install was disabled."
    }
    if (Test-WindowsServer) {
        throw "Docker with the Compose plugin is required but was not found. Docker Desktop is not supported on Windows Server. Install an approved Linux-container runtime first, or run the one-click deployment from a supported Windows 10/11 Pro or Enterprise VM desktop."
    }
    if (-not (Test-DockerDesktopSupportedHost)) {
        throw "Docker with the Compose plugin is required but was not found. Automatic Docker Desktop setup only runs on supported Windows client VMs. Install an approved Docker runtime first, then rerun this script."
    }

    Ensure-WslForDockerDesktop
    $installerPath = Resolve-Installer -Installer $DockerDesktopInstallerUrl -DownloadName "DockerDesktopInstaller.exe"
    Invoke-Installer -InstallerPath $installerPath -Arguments $DockerDesktopInstallerArguments -Name "Docker Desktop"
    Start-DockerDesktop
    Wait-DockerCompose
}

function Ensure-DockerRuntime {
    if (Test-DockerCompose) {
        return
    }
    if ($SkipDependencyInstall) {
        throw "Docker with the Compose plugin is required but was not found. Install your approved Docker runtime, then rerun this script."
    }

    if (-not $DockerInstaller) {
        Write-Host ""
        Write-Host "Docker runtime is required"
        Write-Host "  Where to get it: this script auto-downloads Docker Desktop from Docker's official HTTPS installer on supported Windows 10/11 Pro or Enterprise VMs."
        Write-Host "  Windows Server note: Docker Desktop is not supported on Windows Server. Use a supported client VM for full auto setup, or install an approved Linux-container runtime before rerunning."
        Write-Host "  Docker Desktop Windows docs: https://docs.docker.com/desktop/setup/install/windows-install/"
        Install-DockerDesktopRuntime
        return
    }

    Write-Host ""
    Write-Host "Docker runtime is required"
    Write-Host "  Where to get it: use your organization's approved Docker or container runtime for this Windows Server VM."
    Write-Host "  This app builds a Linux container image, so confirm your runtime supports Linux containers."
    Write-Host "  Docker Desktop Windows docs: https://docs.docker.com/desktop/setup/install/windows-install/"
    Write-Host "  Microsoft Windows container docs: https://learn.microsoft.com/virtualization/windowscontainers/quick-start/set-up-environment"
    Write-Host "  If your runtime is already packaged internally, enter that installer path or URL."

    $installer = $DockerInstaller
    if (-not $installer) {
        $installer = Read-TextInput `
            -Title "Docker/runtime installer path or URL" `
            -Help "Get this from your infrastructure software share, package manager, or approved vendor download. Leave blank only if you will install Docker manually and rerun the script."
    }
    if (-not $installer) {
        throw "Docker with Compose is required before the Gatewatch container can start."
    }

    $installerPath = Resolve-Installer -Installer $installer -DownloadName "gatewatch-docker-runtime-installer.exe"
    $arguments = $DockerInstallerArguments
    if (-not $arguments) {
        $arguments = Read-TextInput `
            -Title "Docker/runtime installer arguments" `
            -Help "Get silent install arguments from the vendor or internal packaging notes. Leave blank to run the installer interactively."
    }
    Invoke-Installer -InstallerPath $installerPath -Arguments $arguments -Name "Docker runtime"

    if (-not (Test-DockerCompose)) {
        Start-DockerDesktop
        Wait-DockerCompose
    }
}

function Read-ProxySecretInput {
    param([string]$ExistingValue)

    if ($ProxySecret) {
        return $ProxySecret
    }
    if ($ExistingValue -and -not (Test-PlaceholderSecret -Value $ExistingValue)) {
        return $ExistingValue
    }

    Write-Host ""
    Write-Host "Proxy shared secret"
    Write-Host "  Where to get it: use an existing value only if your reverse proxy is already configured with one."
    Write-Host "  Most installs should press Enter and let this script generate a 32-byte random secret."
    Write-Host "  Later, copy the generated value from docker\vsphere\.env into the reverse proxy header:"
    Write-Host "  X-Access-Register-Proxy-Secret"
    $secret = Read-Host "  Existing secret, or Enter to generate" -AsSecureString
    $plainSecret = Convert-SecureStringToPlainText -Value $secret
    if ([string]::IsNullOrWhiteSpace($plainSecret)) {
        Write-Info "Generated a new proxy secret and saved it to the env file."
        return New-ProxySecret
    }
    return $plainSecret
}

function Get-ValueFromEnvOrCurrent {
    param(
        [System.Collections.Specialized.OrderedDictionary]$Values,
        [string]$EnvName,
        [string]$CurrentValue
    )
    if ($Values.Contains($EnvName) -and -not [string]::IsNullOrWhiteSpace($Values[$EnvName])) {
        return $Values[$EnvName]
    }
    return $CurrentValue
}

function Get-IntFromEnvOrCurrent {
    param(
        [System.Collections.Specialized.OrderedDictionary]$Values,
        [string]$EnvName,
        [int]$CurrentValue
    )
    if ($Values.Contains($EnvName) -and -not [string]::IsNullOrWhiteSpace($Values[$EnvName])) {
        $parsed = 0
        if (-not [int]::TryParse($Values[$EnvName], [ref]$parsed) -or $parsed -lt 1 -or $parsed -gt 65535) {
            throw "$EnvName in $EnvPath must be a TCP port from 1 to 65535."
        }
        return $parsed
    }
    return $CurrentValue
}

function Initialize-InteractiveConfiguration {
    param([System.Collections.Specialized.OrderedDictionary]$Values)

    $script:Image = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_IMAGE" -CurrentValue $Image
    $script:ContainerName = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_CONTAINER_NAME" -CurrentValue $ContainerName
    $script:DataVolume = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_DATA_VOLUME" -CurrentValue $DataVolume
    $script:NetworkName = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_NETWORK" -CurrentValue $NetworkName
    $script:BindAddress = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_BIND_ADDRESS" -CurrentValue $BindAddress
    $script:AppPort = Get-IntFromEnvOrCurrent -Values $Values -EnvName "GATEWATCH_APP_PORT" -CurrentValue $AppPort
    $script:Scheduler = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_SCHEDULER" -CurrentValue $Scheduler
    $script:AuditEventLog = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_AUDIT_EVENT_LOG" -CurrentValue $AuditEventLog
    $script:AuditEventLogRequired = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED" -CurrentValue $AuditEventLogRequired
    $script:AdminGroups = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_ADMIN_GROUPS" -CurrentValue $AdminGroups
    $script:SupervisorGroups = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_SUPERVISOR_GROUPS" -CurrentValue $SupervisorGroups
    $script:ReviewerGroups = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_REVIEWER_GROUPS" -CurrentValue $ReviewerGroups
    $script:HrGroups = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_HR_GROUPS" -CurrentValue $HrGroups
    $script:ReadOnlyGroups = Get-ValueFromEnvOrCurrent -Values $Values -EnvName "ACCESS_REGISTER_READONLY_GROUPS" -CurrentValue $ReadOnlyGroups

    foreach ($flag in @(
        @{ Name = "ACCESS_REGISTER_SCHEDULER"; Value = $script:Scheduler },
        @{ Name = "ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED"; Value = $script:AuditEventLogRequired }
    )) {
        if ($flag.Value -notin @("0", "1")) {
            throw "$($flag.Name) must be 0 or 1."
        }
    }

    if (-not $PSBoundParameters.ContainsKey("GatewatchUrl") -and (Test-ExampleValue -Value $GatewatchUrl)) {
        $script:GatewatchUrl = Read-TextInput `
            -Title "Production Gatewatch URL" `
            -Help "Get this from the DNS or reverse-proxy plan. It should be the HTTPS URL users will open, such as https://gatewatch.company.local. If DNS is not created yet, enter the planned name. See docs/on-prem-docker-ad-sso.md." `
            -Required
    } else {
        $script:GatewatchUrl = $GatewatchUrl
    }

    if (-not $PSBoundParameters.ContainsKey("AdminGroups") -and (Test-ExampleValue -Value $script:AdminGroups)) {
        $script:AdminGroups = Read-TextInput `
            -Title "Admin AD group" `
            -Help "Get this from AD Users and Computers, PowerShell Get-ADGroup, or Entra admin center. Use the exact group claim format your proxy sends, usually DOMAIN\GroupName. Example: DOMAIN\AccessRegister-Admins." `
            -Required
    }
    if (-not $PSBoundParameters.ContainsKey("SupervisorGroups") -and (Test-ExampleValue -Value $script:SupervisorGroups)) {
        $script:SupervisorGroups = Read-TextInput `
            -Title "Supervisor AD group" `
            -Help "Get this from the access-review or manager workflow owner. Example: DOMAIN\AccessRegister-Supervisors. Leave blank only if Supervisor rollout is not ready."
    }
    if (-not $PSBoundParameters.ContainsKey("ReviewerGroups") -and (Test-ExampleValue -Value $script:ReviewerGroups)) {
        $script:ReviewerGroups = Read-TextInput `
            -Title "Reviewer AD group" `
            -Help "Get this from the team that certifies access reviews. Example: DOMAIN\AccessRegister-Reviewers. Leave blank only if Reviewer rollout is not ready."
    }
    if (-not $PSBoundParameters.ContainsKey("HrGroups") -and (Test-ExampleValue -Value $script:HrGroups)) {
        $script:HrGroups = Read-TextInput `
            -Title "HR AD group" `
            -Help "Get this from HRIS or AD administrators. It controls employee and offboarding workflows. Example: DOMAIN\AccessRegister-HR. Leave blank only if HR rollout is not ready."
    }
    if (-not $PSBoundParameters.ContainsKey("ReadOnlyGroups") -and (Test-ExampleValue -Value $script:ReadOnlyGroups)) {
        $script:ReadOnlyGroups = Read-TextInput `
            -Title "Read-only AD group" `
            -Help "Get this from the audit, security, or operations viewer group. Example: DOMAIN\AccessRegister-ReadOnly. Leave blank only if read-only users are not ready."
    }

    if (-not $PSBoundParameters.ContainsKey("BindAddress") -and -not $PSBoundParameters.ContainsKey("AllowedProxyRemoteAddress")) {
        $sameVmProxy = Read-YesNo `
            -Title "Will the reverse proxy run on this same VM?" `
            -Help "Choose yes for the simplest pilot. The app will bind to 127.0.0.1:8087 so users cannot reach it directly. Choose no only when another proxy host must connect to this VM." `
            -DefaultYes $true
        if ($sameVmProxy) {
            $script:BindAddress = "127.0.0.1"
            $script:AllowedProxyRemoteAddress = $AllowedProxyRemoteAddress
        } else {
            $script:BindAddress = Read-TextInput `
                -Title "App bind address for external reverse proxy" `
                -Help "Use the VM interface address or 0.0.0.0 only when Windows Firewall will restrict TCP 8087 to the proxy host." `
                -DefaultValue "0.0.0.0" `
                -Required
            $script:AllowedProxyRemoteAddress = Read-TextInput `
                -Title "Allowed reverse proxy IP or subnet" `
                -Help "Get this from the reverse proxy server's static IP or the infrastructure team. Example: 10.20.30.15 or 10.20.30.0/24." `
                -Required
        }
    } else {
        $script:AllowedProxyRemoteAddress = $AllowedProxyRemoteAddress
    }

    $script:ProxySecret = Read-ProxySecretInput -ExistingValue $Values["ACCESS_REGISTER_PROXY_SECRET"]

    if ($SkipAdSyncTaskPrompt) {
        $script:RegisterAdSyncTask = $false
    } elseif (-not $PSBoundParameters.ContainsKey("RegisterAdSyncTask")) {
        $script:RegisterAdSyncTask = Read-YesNo `
            -Title "Register the production AD sync scheduled task now?" `
            -Help "Choose yes if the gMSA already exists and this VM can run the ActiveDirectory PowerShell module. Choose no to configure it later from docs/production-checklist.md." `
            -DefaultYes $false
    }

    if ($script:RegisterAdSyncTask) {
        if (-not $AdSyncServiceAccount) {
            $script:AdSyncServiceAccount = Read-TextInput `
                -Title "AD sync gMSA service account" `
                -Help "Get this from the AD admin who created the group managed service account. It usually ends with $, for example DOMAIN\gmsa-gatewatch-adsync$." `
                -Required
        }
        if (-not $AdSyncSearchBase) {
            $script:AdSyncSearchBase = Read-TextInput `
                -Title "AD sync SearchBase" `
                -Help "Get this from AD Users and Computers by finding the OU that contains users, then use its Distinguished Name. Example: OU=Users,DC=company,DC=local." `
                -Required
        }
        if (-not $PSBoundParameters.ContainsKey("AdSyncDirectLocal")) {
            $script:AdSyncDirectLocal = Read-YesNo `
                -Title "Should AD sync call the local app port directly?" `
                -Help "Choose no if the sync account can authenticate through the HTTPS reverse proxy. Choose yes only for a controlled same-VM job using trusted headers and the proxy secret." `
                -DefaultYes $false
        }
        if ($script:AdSyncDirectLocal -and -not $AdSyncRemoteGroups) {
            $script:AdSyncRemoteGroups = Read-TextInput `
                -Title "AD sync trusted remote group header" `
                -Help "Use the Gatewatch Admin mapping group claim that the service account should present, usually DOMAIN\AccessRegister-Admins." `
                -DefaultValue $script:AdminGroups `
                -Required
        }
    }
}

function New-ProxySecret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes)
}

function Convert-ToSshGitUrl {
    param([string]$RepoUrl)
    if ($RepoUrl -match "^https://github\.com/([^/]+)/([^/]+?)(\.git)?$") {
        return "git@github.com:$($Matches[1])/$($Matches[2]).git"
    }
    return $RepoUrl
}

function Protect-PrivateFile {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    try {
        $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls $Path /inheritance:r | Out-Null
        & icacls $Path /grant:r "${currentIdentity}:(R,W)" "Administrators:(F)" "SYSTEM:(F)" | Out-Null
        Write-Info "Restricted $Description ACL to the current user, Administrators, and SYSTEM."
    } catch {
        Write-Warn "Could not harden $Description ACL: $($_.Exception.Message)"
    }
}

function Initialize-GitHubDeployKey {
    if ($UseExistingGitAuth -or -not $PrivateGitHubRepo) {
        return ""
    }

    Ensure-OpenSshClient

    if (-not $DeployKeyPath) {
        $DeployKeyPath = Join-Path $InstallRoot "keys\github_deploy_ed25519"
    } else {
        $DeployKeyPath = Resolve-FullPath $DeployKeyPath
    }
    $script:EffectiveDeployKeyPath = $DeployKeyPath

    $keyDirectory = Split-Path -Parent $DeployKeyPath
    New-Item -ItemType Directory -Force -Path $keyDirectory | Out-Null

    if (-not (Test-Path -LiteralPath $DeployKeyPath -PathType Leaf)) {
        Write-Step "Generate GitHub deploy key"
        $comment = "gatewatch-production-$env:COMPUTERNAME"
        Invoke-External -FilePath "ssh-keygen" -Arguments @(
            "-t",
            "ed25519",
            "-C",
            $comment,
            "-f",
            $DeployKeyPath,
            "-N",
            ""
        ) -FailureMessage "GitHub deploy key generation failed."
        Protect-PrivateFile -Path $DeployKeyPath -Description "GitHub deploy key"
    }

    $publicKeyPath = "$DeployKeyPath.pub"
    Assert-FileExists -Path $publicKeyPath -Description "GitHub deploy public key"
    $publicKey = Get-Content -LiteralPath $publicKeyPath -Raw

    Write-Host ""
    Write-Host "GitHub deploy key required"
    Write-Host "  Private repo mode is enabled. Add this public key as a READ-ONLY deploy key:"
    Write-Host ""
    Write-Host $publicKey.Trim()
    Write-Host ""
    Write-Host "  Where to add it:"
    Write-Host "  $GitHubDeployKeysUrl"
    Write-Host ""
    Write-Host "  GitHub path: repository Settings > Deploy keys > Add deploy key."
    Write-Host "  Title suggestion: Gatewatch production $env:COMPUTERNAME"
    Write-Host "  Do not check 'Allow write access'."
    Write-Host ""
    Read-Host "Press Enter after the deploy key is added to GitHub"

    return $DeployKeyPath
}

function Invoke-Git {
    param(
        [string[]]$Arguments,
        [string]$DeployKey
    )
    $previousGitSshCommand = $env:GIT_SSH_COMMAND
    try {
        if ($DeployKey) {
            $env:GIT_SSH_COMMAND = "ssh -i `"$DeployKey`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        }
        Invoke-External -FilePath "git" -Arguments $Arguments -FailureMessage "git command failed."
    } finally {
        $env:GIT_SSH_COMMAND = $previousGitSshCommand
    }
}

function Sync-GitRepository {
    if ($SkipGitFetch) {
        Write-Info "Skipping GitHub fetch because -SkipGitFetch was passed."
        return
    }

    Ensure-Git
    $repoUrlForGit = $GitRepoUrl
    $deployKey = ""
    if ($PrivateGitHubRepo -and -not $UseExistingGitAuth) {
        $repoUrlForGit = Convert-ToSshGitUrl -RepoUrl $GitRepoUrl
        $deployKey = Initialize-GitHubDeployKey
    }
    $script:EffectiveGitRepoUrl = $repoUrlForGit

    $appGitPath = Join-Path $AppRoot ".git"
    if (Test-Path -LiteralPath $appGitPath -PathType Container) {
        Write-Step "Update Gatewatch source from GitHub"
        Invoke-Git -DeployKey $deployKey -Arguments @("-C", $AppRoot, "fetch", "--prune", "origin")
        Invoke-Git -DeployKey $deployKey -Arguments @("-C", $AppRoot, "checkout", $GitBranch)
        Invoke-Git -DeployKey $deployKey -Arguments @("-C", $AppRoot, "pull", "--ff-only", "origin", $GitBranch)
        return
    }

    if (Test-Path -LiteralPath $AppRoot -PathType Container) {
        $existingItems = @(Get-ChildItem -LiteralPath $AppRoot -Force)
        if ($existingItems.Count -gt 0) {
            throw "AppRoot '$AppRoot' exists but is not a Git checkout and is not empty. Move it aside or pass -AppRoot with an empty folder."
        }
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $AppRoot) | Out-Null
    }

    Write-Step "Clone Gatewatch from GitHub"
    Invoke-Git -DeployKey $deployKey -Arguments @("clone", "--branch", $GitBranch, "--single-branch", $repoUrlForGit, $AppRoot)
}

function Read-EnvFile {
    param([string]$Path)
    $values = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separatorIndex).Trim()
        $value = $line.Substring($separatorIndex + 1).Trim()
        $values[$name] = $value
    }
    return $values
}

function Set-EnvValue {
    param(
        [System.Collections.Specialized.OrderedDictionary]$Values,
        [string]$Name,
        [string]$Value
    )
    Assert-SafeEnvValue -Name $Name -Value $Value
    $Values[$Name] = $Value
}

function Write-EnvFile {
    param(
        [string]$Path,
        [System.Collections.Specialized.OrderedDictionary]$Values
    )
    $lines = @(
        "# Generated by scripts/install-gatewatch-production.ps1.",
        "# Treat this file as sensitive. Do not commit it.",
        "",
        "GATEWATCH_IMAGE=$($Values["GATEWATCH_IMAGE"])",
        "GATEWATCH_CONTAINER_NAME=$($Values["GATEWATCH_CONTAINER_NAME"])",
        "GATEWATCH_DATA_VOLUME=$($Values["GATEWATCH_DATA_VOLUME"])",
        "GATEWATCH_NETWORK=$($Values["GATEWATCH_NETWORK"])",
        "",
        "GATEWATCH_BIND_ADDRESS=$($Values["GATEWATCH_BIND_ADDRESS"])",
        "GATEWATCH_APP_PORT=$($Values["GATEWATCH_APP_PORT"])",
        "",
        "ACCESS_REGISTER_SCHEDULER=$($Values["ACCESS_REGISTER_SCHEDULER"])",
        "ACCESS_REGISTER_PROXY_SECRET=$($Values["ACCESS_REGISTER_PROXY_SECRET"])",
        "ACCESS_REGISTER_AUDIT_EVENT_LOG=$($Values["ACCESS_REGISTER_AUDIT_EVENT_LOG"])",
        "ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED=$($Values["ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED"])",
        "",
        "ACCESS_REGISTER_ADMIN_GROUPS=$($Values["ACCESS_REGISTER_ADMIN_GROUPS"])",
        "ACCESS_REGISTER_SUPERVISOR_GROUPS=$($Values["ACCESS_REGISTER_SUPERVISOR_GROUPS"])",
        "ACCESS_REGISTER_REVIEWER_GROUPS=$($Values["ACCESS_REGISTER_REVIEWER_GROUPS"])",
        "ACCESS_REGISTER_HR_GROUPS=$($Values["ACCESS_REGISTER_HR_GROUPS"])",
        "ACCESS_REGISTER_READONLY_GROUPS=$($Values["ACCESS_REGISTER_READONLY_GROUPS"])"
    )
    Set-Content -LiteralPath $Path -Value $lines -Encoding ASCII
}

function Protect-EnvFile {
    param([string]$Path)
    if ($SkipEnvAclHardening) {
        Write-Info "Skipping env file ACL hardening."
        return
    }

    if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
        Write-Warn "Skipping env file ACL hardening because this does not look like Windows PowerShell on Windows."
        return
    }

    try {
        $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls $Path /inheritance:r | Out-Null
        & icacls $Path /grant:r "${currentIdentity}:(R,W)" "Administrators:(F)" "SYSTEM:(F)" | Out-Null
        Write-Info "Restricted env file ACL to the current user, Administrators, and SYSTEM."
    } catch {
        Write-Warn "Could not harden env file ACL: $($_.Exception.Message)"
    }
}

function Invoke-External {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    Write-Host "> $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE"
    }
}

function Invoke-DockerCompose {
    param([string[]]$ComposeArguments)
    $arguments = @(
        "compose",
        "--env-file",
        $script:EnvPath,
        "-f",
        $script:ComposePath
    ) + $ComposeArguments
    Invoke-External -FilePath "docker" -Arguments $arguments -FailureMessage "docker compose failed."
}

function Get-HealthUrl {
    "http://127.0.0.1:$script:AppPort/healthz"
}

function Wait-GatewatchHealth {
    param(
        [int]$Attempts = 30,
        [int]$DelaySeconds = 2
    )

    $healthUrl = Get-HealthUrl
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
            if ($response.status -eq "ok" -and $response.database -eq "ok") {
                Write-Info "Health check passed at $healthUrl."
                return $response
            }
            $lastError = "Unexpected health payload: $($response | ConvertTo-Json -Compress)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    throw "Gatewatch did not become healthy at $healthUrl. Last error: $lastError"
}

function Ensure-ExternalProxyFirewallRule {
    if (Test-LoopbackBind -Address $BindAddress) {
        Write-Info "App port is bound to loopback. No app-port firewall rule is needed."
        return
    }
    if (-not $AllowedProxyRemoteAddress) {
        throw "Non-loopback BindAddress requires -AllowedProxyRemoteAddress so TCP $AppPort is restricted to the reverse proxy."
    }
    if ($SkipFirewallRule) {
        Write-Warn "Skipping firewall rule creation for non-loopback app bind. Confirm TCP $AppPort is restricted to $AllowedProxyRemoteAddress before users can reach the VM."
        return
    }
    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Write-Warn "New-NetFirewallRule is unavailable. Confirm TCP $AppPort is restricted to $AllowedProxyRemoteAddress manually."
        return
    }

    $existing = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Warn "Firewall rule '$FirewallRuleName' already exists. Review it manually and confirm it restricts TCP $AppPort to $AllowedProxyRemoteAddress."
        return
    }

    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $AppPort `
        -RemoteAddress $AllowedProxyRemoteAddress `
        -Profile Domain | Out-Null
    Write-Info "Created firewall rule '$FirewallRuleName' for TCP $AppPort from $AllowedProxyRemoteAddress."
}

function New-AdSyncWrapper {
    param([string]$WrapperPath)

    $targetUrl = $GatewatchUrl
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$script:SyncScriptPath`"",
        "-GatewatchUrl",
        "`"$targetUrl`"",
        "-SearchBase",
        "`"$AdSyncSearchBase`"",
        "-Json"
    )

    if ($AdSyncDirectLocal) {
        $targetUrl = "http://127.0.0.1:$AppPort"
        $arguments = @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "`"$script:SyncScriptPath`"",
            "-GatewatchUrl",
            "`"$targetUrl`"",
            "-SearchBase",
            "`"$AdSyncSearchBase`"",
            "-RemoteUser",
            "`"$AdSyncRemoteUser`"",
            "-RemoteGroups",
            "`"$AdSyncRemoteGroups`"",
            "-Json"
        )
    } else {
        $arguments += "-UseDefaultCredentialsForSso"
    }

    if ($AdSyncRouteDisabledAccess) {
        $arguments += "-RouteDisabledAccess"
    }

    $wrapperLines = @(
        '$ErrorActionPreference = "Stop"',
        ('Set-Location "{0}"' -f $AppRoot),
        ('if (Test-Path -LiteralPath "{0}") {{' -f $EnvPath),
        ('  foreach ($line in Get-Content -LiteralPath "{0}") {{' -f $EnvPath),
        '    if ($line -match "^ACCESS_REGISTER_PROXY_SECRET=(.+)$") { $env:ACCESS_REGISTER_PROXY_SECRET = $Matches[1] }',
        '  }',
        '}',
        ('& powershell.exe {0}' -f ($arguments -join " "))
    )
    Set-Content -LiteralPath $WrapperPath -Value $wrapperLines -Encoding ASCII
}

function Register-AdSyncScheduledTask {
    if (-not $RegisterAdSyncTask) {
        return
    }
    if (-not $AdSyncServiceAccount) {
        throw "-RegisterAdSyncTask requires -AdSyncServiceAccount. Use a gMSA such as DOMAIN\gmsa-gatewatch-adsync$."
    }
    if (-not $AdSyncServiceAccount.EndsWith("$")) {
        throw "This installer registers AD sync tasks only for gMSA service accounts. Use a name ending in '$', or register a password-based task manually."
    }
    if (-not $AdSyncSearchBase) {
        throw "-RegisterAdSyncTask requires -AdSyncSearchBase."
    }
    if ($AdSyncDirectLocal -and -not $AdSyncRemoteGroups) {
        throw "-AdSyncDirectLocal requires -AdSyncRemoteGroups so Gatewatch can map the sync account to Admin."
    }

    Assert-FileExists -Path $script:SyncScriptPath -Description "AD sync script"
    $wrapperPath = Join-Path (Split-Path -Parent $EnvPath) "gatewatch-ad-sync-task.local.ps1"
    New-AdSyncWrapper -WrapperPath $wrapperPath

    $existing = Get-ScheduledTask -TaskName $AdSyncTaskName -ErrorAction SilentlyContinue
    if ($existing) {
        if (-not $ForceEnv) {
            throw "Scheduled task '$AdSyncTaskName' already exists. Pass -ForceEnv to replace it."
        }
        Unregister-ScheduledTask -TaskName $AdSyncTaskName -Confirm:$false
    }

    $startAt = [datetime]::Today.Add([TimeSpan]::Parse($AdSyncStartTime))
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapperPath`""
    $trigger = New-ScheduledTaskTrigger -Daily -At $startAt
    $principal = New-ScheduledTaskPrincipal `
        -UserId $AdSyncServiceAccount `
        -LogonType ServiceAccount `
        -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    Register-ScheduledTask `
        -TaskName $AdSyncTaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Runs Gatewatch production Active Directory sync." | Out-Null

    Write-Info "Registered AD sync scheduled task '$AdSyncTaskName'."
}

function Write-HandoffFile {
    param(
        [AllowNull()]
        $Health
    )

    $groupWarning = @()
    foreach ($groupValue in @($AdminGroups, $SupervisorGroups, $ReviewerGroups, $HrGroups, $ReadOnlyGroups)) {
        if ($groupValue -like "DOMAIN\*") {
            $groupWarning += $groupValue
        }
    }

    $healthLine = "not run"
    if ($Health) {
        $healthLine = "status=$($Health.status); database=$($Health.database); checked_at=$($Health.checked_at)"
    }

    $lines = @(
        "Gatewatch production install handoff",
        "Generated: $((Get-Date).ToUniversalTime().ToString("o"))",
        "",
        "App root: $AppRoot",
        "Git repo: $GitRepoUrl",
        "Git branch: $GitBranch",
        "Env file: $EnvPath",
        "Compose file: $ComposePath",
        "Container: $ContainerName",
        "Image: $Image",
        "Data volume: $DataVolume",
        "Network: $NetworkName",
        "Bind: ${BindAddress}:$AppPort",
        "Health: $healthLine",
        "",
        "Proxy secret:",
        "  Stored only in the env file. Retrieve it on the VM with:",
        "  Select-String -Path `"$EnvPath`" -Pattern '^ACCESS_REGISTER_PROXY_SECRET='",
        "",
        "Required next steps:",
        "  1. Configure DNS and TLS for $GatewatchUrl.",
        "  2. Configure the AD-authenticated reverse proxy.",
        "  3. Proxy must strip inbound identity headers.",
        "  4. Proxy must inject X-Remote-User, X-Remote-Email, X-Remote-Name, X-Remote-Groups.",
        "  5. Proxy must inject X-Access-Register-Proxy-Secret from the env file.",
        "  6. Confirm user networks cannot reach TCP $AppPort directly.",
        "  7. Configure infrastructure backup for the VM and Docker volume.",
        "  8. Configure log shipping for /data/audit-events.jsonl.",
        "  9. Complete the smoke test in docs/production-checklist.md."
    )

    if ($groupWarning.Count -gt 0) {
        $lines += ""
        $lines += "Warning:"
        $lines += "  Some role groups still use DOMAIN example values. Replace them with real production groups before user testing."
    }

    if ($RegisterAdSyncTask) {
        $lines += ""
        $lines += "AD sync task:"
        $lines += "  Task name: $AdSyncTaskName"
        $lines += "  Service account: $AdSyncServiceAccount"
        $lines += "  Search base: $AdSyncSearchBase"
    }

    if ($script:EffectiveDeployKeyPath) {
        $lines += ""
        $lines += "GitHub deploy key:"
        $lines += "  Private key path on VM: $($script:EffectiveDeployKeyPath)"
        $lines += "  Public key path on VM: $($script:EffectiveDeployKeyPath).pub"
        $lines += "  GitHub deploy-key settings: $GitHubDeployKeysUrl"
        $lines += "  Keep this key read-only in GitHub."
    }

    Set-Content -LiteralPath $HandoffPath -Value $lines -Encoding ASCII
    Write-Info "Wrote non-secret handoff file: $HandoffPath"
}

if (-not $InstallRoot) {
    $InstallRoot = Get-DefaultInstallRoot
}
$InstallRoot = Resolve-FullPath $InstallRoot

if (-not $AppRoot) {
    $repoCandidate = Resolve-FullPath (Join-Path $PSScriptRoot "..")
    if (Test-Path -LiteralPath (Join-Path $repoCandidate "app.py") -PathType Leaf) {
        $AppRoot = $repoCandidate
    } else {
        $AppRoot = Join-Path $InstallRoot "app"
    }
} else {
    $AppRoot = Resolve-FullPath $AppRoot
}

Write-Step "Fetch Gatewatch source"
Sync-GitRepository

if (-not $EnvPath) {
    $EnvPath = Join-Path $AppRoot "docker\vsphere\.env"
} else {
    $EnvPath = Resolve-FullPath $EnvPath
}

$script:EnvPath = $EnvPath
$script:ComposePath = Join-Path $AppRoot "docker\vsphere\compose.yaml"
$script:SyncScriptPath = Join-Path $AppRoot "scripts\sync-active-directory.ps1"
$script:AppPort = $AppPort

if (-not $HandoffPath) {
    $HandoffPath = Join-Path (Split-Path -Parent $EnvPath) "deployment-handoff.txt"
} else {
    $HandoffPath = Resolve-FullPath $HandoffPath
}

$envDirectory = Split-Path -Parent $EnvPath
New-Item -ItemType Directory -Force -Path $envDirectory | Out-Null
$envValues = Read-EnvFile -Path $EnvPath

if ((Test-Path -LiteralPath $EnvPath) -and -not $ForceEnv) {
    Write-Info "Existing env file found. Existing values are preserved unless a parameter or prompt supplies a replacement."
}

Write-Step "Collect production settings"
Initialize-InteractiveConfiguration -Values $envValues

Write-Step "Validate repository and tools"
Assert-DirectoryExists -Path $AppRoot -Description "Gatewatch app root"
Assert-FileExists -Path (Join-Path $AppRoot "app.py") -Description "Gatewatch app"
Assert-FileExists -Path (Join-Path $AppRoot "Dockerfile") -Description "Gatewatch Dockerfile"
Assert-FileExists -Path $script:ComposePath -Description "vSphere Compose file"
Assert-FileExists -Path (Join-Path $AppRoot "docker\vsphere\.env.example") -Description "vSphere env example"
Ensure-DockerRuntime
Invoke-External -FilePath "docker" -Arguments @("version") -FailureMessage "Docker is not available."
Invoke-External -FilePath "docker" -Arguments @("compose", "version") -FailureMessage "Docker Compose is not available."

if (-not (Test-LoopbackBind -Address $BindAddress) -and -not $AllowedProxyRemoteAddress) {
    throw "BindAddress '$BindAddress' is not loopback. Pass -AllowedProxyRemoteAddress with the reverse proxy IP or subnet."
}

if ($RunVerification) {
    Write-Step "Run optional repository verification"
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "-RunVerification was requested, but python was not found on PATH."
    }
    Invoke-External -FilePath "python" -Arguments @("scripts\verify.py", "--docker") -FailureMessage "Gatewatch verification failed."
}

Write-Step "Create production env file"
Set-EnvValue -Values $envValues -Name "GATEWATCH_IMAGE" -Value $Image
Set-EnvValue -Values $envValues -Name "GATEWATCH_CONTAINER_NAME" -Value $ContainerName
Set-EnvValue -Values $envValues -Name "GATEWATCH_DATA_VOLUME" -Value $DataVolume
Set-EnvValue -Values $envValues -Name "GATEWATCH_NETWORK" -Value $NetworkName
Set-EnvValue -Values $envValues -Name "GATEWATCH_BIND_ADDRESS" -Value $BindAddress
Set-EnvValue -Values $envValues -Name "GATEWATCH_APP_PORT" -Value ([string]$AppPort)
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_SCHEDULER" -Value $Scheduler
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_PROXY_SECRET" -Value $ProxySecret
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_AUDIT_EVENT_LOG" -Value $AuditEventLog
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED" -Value $AuditEventLogRequired
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_ADMIN_GROUPS" -Value $AdminGroups
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_SUPERVISOR_GROUPS" -Value $SupervisorGroups
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_REVIEWER_GROUPS" -Value $ReviewerGroups
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_HR_GROUPS" -Value $HrGroups
Set-EnvValue -Values $envValues -Name "ACCESS_REGISTER_READONLY_GROUPS" -Value $ReadOnlyGroups

Write-EnvFile -Path $EnvPath -Values $envValues
Protect-EnvFile -Path $EnvPath
Write-Info "Env file ready: $EnvPath"

Write-Step "Validate Docker Compose configuration"
Invoke-DockerCompose -ComposeArguments @("config", "--quiet")

Ensure-ExternalProxyFirewallRule

$health = $null
if (-not $SkipStart) {
    Write-Step "Start Gatewatch container"
    if ($SkipBuild) {
        Invoke-DockerCompose -ComposeArguments @("up", "-d")
    } else {
        Invoke-DockerCompose -ComposeArguments @("up", "-d", "--build")
    }
    Invoke-DockerCompose -ComposeArguments @("ps")
    Invoke-DockerCompose -ComposeArguments @("logs", "--tail", "100", "app")

    if (-not $SkipHealthCheck) {
        Write-Step "Run local health check"
        $health = Wait-GatewatchHealth
    }
} else {
    Write-Warn "Skipping container startup."
}

Write-Step "Optional AD sync scheduled task"
Register-AdSyncScheduledTask

Write-Step "Write handoff"
Write-HandoffFile -Health $health

Write-Step "Complete"
Write-Info "Gatewatch VM-local Docker setup completed."
Write-Info "Do not paste the proxy secret into tickets or logs. Read it from the env file only when configuring the trusted reverse proxy."
Write-Info "Next: configure DNS, TLS, AD SSO reverse proxy headers, backups, and the production smoke test from docs/production-checklist.md."
