<#
.SYNOPSIS
One-click Gatewatch deployment launcher for a downloaded Desktop folder.

.DESCRIPTION
Double-click Deploy-Gatewatch.cmd from the downloaded Gatewatch folder on the
Windows Server VM desktop. This launcher self-elevates, copies the downloaded
files into D:\AccessRegister\app or C:\AccessRegister\app, then runs
scripts\install-gatewatch-production.ps1 from that install folder.

The production installer continues to prompt for site-specific values and tells
you where to get each one.
#>

[CmdletBinding()]
param(
    [string]$InstallRoot,
    [switch]$NoElevate,
    [switch]$UseSourceInPlace,
    [string[]]$InstallerArguments = @()
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
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

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Quote-Argument {
    param([string]$Value)
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Restart-Elevated {
    $arguments = @(
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Quote-Argument $PSCommandPath)
    )
    if ($InstallRoot) {
        $arguments += @("-InstallRoot", (Quote-Argument $InstallRoot))
    }
    if ($UseSourceInPlace) {
        $arguments += "-UseSourceInPlace"
    }
    if ($InstallerArguments.Count -gt 0) {
        $arguments += "-InstallerArguments"
    }
    foreach ($installerArgument in $InstallerArguments) {
        $arguments += (Quote-Argument $installerArgument)
    }

    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs | Out-Null
}

function Assert-SourceFolder {
    param([string]$SourceRoot)
    $required = @(
        "app.py",
        "Dockerfile",
        "docker\vsphere\compose.yaml",
        "scripts\install-gatewatch-production.ps1"
    )
    foreach ($relativePath in $required) {
        $path = Join-Path $SourceRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Downloaded Gatewatch folder is missing $relativePath. Download the full repository folder and run Deploy-Gatewatch.cmd from its top level."
        }
    }
}

function Copy-SourceToInstallRoot {
    param(
        [string]$SourceRoot,
        [string]$TargetRoot
    )

    $sourceFull = Resolve-FullPath $SourceRoot
    $targetFull = Resolve-FullPath $TargetRoot
    if ($sourceFull.TrimEnd("\") -ieq $targetFull.TrimEnd("\")) {
        Write-Host "Source folder is already the install folder."
        return
    }

    New-Item -ItemType Directory -Force -Path $targetFull | Out-Null
    $robocopyArgs = @(
        $sourceFull,
        $targetFull,
        "/E",
        "/R:2",
        "/W:2",
        "/NFL",
        "/NDL",
        "/NP",
        "/XD",
        ".git",
        ".agents",
        ".codex",
        ".pytest_cache",
        "__pycache__",
        "data",
        "output",
        "/XF",
        "*.pyc",
        "*.log",
        ".env",
        "deployment-handoff.txt",
        "gatewatch-ad-sync-task.local.ps1"
    )

    Write-Host "> robocopy $sourceFull $targetFull"
    & robocopy @robocopyArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -gt 7) {
        throw "Copy to install folder failed. Robocopy exit code: $exitCode"
    }
}

if (-not $InstallRoot) {
    $InstallRoot = Get-DefaultInstallRoot
}
$InstallRoot = Resolve-FullPath $InstallRoot
$sourceRoot = Resolve-FullPath $PSScriptRoot

if (-not $NoElevate -and -not (Test-IsAdministrator)) {
    Write-Host "Gatewatch deployment needs an elevated PowerShell window for install-folder ACLs, Docker, and optional firewall/task setup."
    Write-Host "Approving the prompt will open the real deployment window."
    Restart-Elevated
    return
}

Write-Step "Validate downloaded Gatewatch folder"
Assert-SourceFolder -SourceRoot $sourceRoot

$appRoot = if ($UseSourceInPlace) { $sourceRoot } else { Join-Path $InstallRoot "app" }
if (-not $UseSourceInPlace) {
    Write-Step "Copy downloaded files to install folder"
    Copy-SourceToInstallRoot -SourceRoot $sourceRoot -TargetRoot $appRoot
}

$installerPath = Join-Path $appRoot "scripts\install-gatewatch-production.ps1"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Installer script was not found after copy: $installerPath"
}

Write-Step "Run Gatewatch production installer"
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $installerPath,
    "-InstallRoot",
    $InstallRoot,
    "-AppRoot",
    $appRoot,
    "-SkipGitFetch"
) + $InstallerArguments

Write-Host "> powershell.exe -File $installerPath"
$processArguments = $arguments | ForEach-Object { Quote-Argument ([string]$_) }
$installerProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $processArguments -NoNewWindow -Wait -PassThru
$installerExitCode = $installerProcess.ExitCode
if ($installerExitCode -ne 0) {
    throw "Gatewatch production installer failed. Exit code: $installerExitCode"
}

$global:LASTEXITCODE = 0
