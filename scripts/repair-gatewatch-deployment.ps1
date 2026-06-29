<#
.SYNOPSIS
Downloads a fresh public Gatewatch copy and starts the one-click deployment.

.DESCRIPTION
Use this when a VM or laptop has a stale Gatewatch download or install folder
from an earlier failed deployment. The script downloads the public GitHub main
archive over HTTPS into a new Desktop folder, then runs Deploy-Gatewatch.ps1
from that fresh copy. Existing Docker volumes and env files are not deleted.
#>

[CmdletBinding()]
param(
    [string]$DestinationRoot,
    [string]$ArchiveUrl = "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.zip",
    [switch]$SkipDeploy,
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

function Resolve-FullPath {
    param([string]$Path)
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Quote-Argument {
    param([string]$Value)
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-HttpsUrl {
    param(
        [string]$Url,
        [string]$Description
    )
    try {
        $uri = [Uri]$Url
    } catch {
        throw "$Description must be a valid HTTPS URL."
    }
    if ($uri.Scheme -ne "https") {
        throw "$Description must use HTTPS."
    }
}

function Get-DefaultDestinationRoot {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $desktop) {
        $desktop = $PWD.Path
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $desktop "Gatewatch-repair-$stamp"
}

function Restart-Elevated {
    if (-not $PSCommandPath) {
        throw "Save this repair script to disk and run it with powershell.exe -File so it can self-elevate."
    }

    $arguments = @(
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Quote-Argument $PSCommandPath)
    )
    if ($DestinationRoot) {
        $arguments += @("-DestinationRoot", (Quote-Argument $DestinationRoot))
    }
    if ($ArchiveUrl) {
        $arguments += @("-ArchiveUrl", (Quote-Argument $ArchiveUrl))
    }
    if ($SkipDeploy) {
        $arguments += "-SkipDeploy"
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

function Get-DownloadedGatewatchRoot {
    param([string]$Root)

    $candidate = Get-ChildItem -LiteralPath $Root -Directory |
        Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName "Deploy-Gatewatch.ps1") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "app.py") -PathType Leaf)
        } |
        Select-Object -First 1
    if (-not $candidate) {
        throw "The downloaded Gatewatch archive did not contain the expected repository root."
    }
    return $candidate.FullName
}

function Invoke-Deploy {
    param([string]$RepoRoot)

    $deployScript = Join-Path $RepoRoot "Deploy-Gatewatch.ps1"
    if (-not (Test-Path -LiteralPath $deployScript -PathType Leaf)) {
        throw "Deploy-Gatewatch.ps1 was not found in $RepoRoot."
    }

    Write-Step "Run fresh Gatewatch deployment"
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $deployScript,
        "-NoElevate"
    )
    if ($UseSourceInPlace) {
        $arguments += "-UseSourceInPlace"
    }
    if ($InstallerArguments.Count -gt 0) {
        $arguments += "-InstallerArguments"
    }
    foreach ($installerArgument in $InstallerArguments) {
        $arguments += $installerArgument
    }

    Write-Host "> powershell.exe -File $deployScript"
    $processArguments = $arguments | ForEach-Object { Quote-Argument ([string]$_) }
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $processArguments -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Fresh Gatewatch deployment failed. Exit code: $($process.ExitCode)"
    }
    $global:LASTEXITCODE = 0
}

Assert-HttpsUrl -Url $ArchiveUrl -Description "Gatewatch source archive URL"

if (-not $NoElevate -and -not (Test-IsAdministrator)) {
    Write-Host "Gatewatch repair will open an elevated PowerShell window so deployment can write the install folder and use Docker."
    Restart-Elevated
    return
}

if (-not $DestinationRoot) {
    $DestinationRoot = Get-DefaultDestinationRoot
}
$DestinationRoot = Resolve-FullPath $DestinationRoot

Write-Step "Download fresh Gatewatch files"
New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null
$zipPath = Join-Path $DestinationRoot "Gatewatch-main.zip"
Write-Host "> Invoke-WebRequest $ArchiveUrl"
Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zipPath
Expand-Archive -LiteralPath $zipPath -DestinationPath $DestinationRoot -Force
$repoRoot = Get-DownloadedGatewatchRoot -Root $DestinationRoot

Write-Host ""
Write-Host "Fresh Gatewatch folder:"
Write-Host "  $repoRoot"

if (-not $SkipDeploy) {
    Invoke-Deploy -RepoRoot $repoRoot
} else {
    Write-Host ""
    Write-Host "Skipped deployment. Run Deploy-Gatewatch.cmd from the fresh folder when ready."
}
