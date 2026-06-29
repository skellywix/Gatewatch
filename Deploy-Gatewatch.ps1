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

param(
    [string]$InstallRoot,
    [switch]$NoElevate,
    [switch]$UseSourceInPlace,
    [switch]$SkipSelfUpdate,
    [string]$SourceArchiveUrl = "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.zip",
    [string]$InstallerArgumentsJson,
    [string]$InstallerArgumentsBase64,
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

function Write-Info {
    param([string]$Message)
    Write-Host "[info] $Message"
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

function Convert-ArgumentsToJson {
    param([string[]]$Arguments)
    $encoded = @($Arguments | ForEach-Object { ConvertTo-Json -Compress -InputObject ([string]$_) })
    return "[" + ($encoded -join ",") + "]"
}

function Convert-ArgumentsToBase64 {
    param([string[]]$Arguments)
    $json = Convert-ArgumentsToJson -Arguments $Arguments
    return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
}

function Convert-Base64ToJson {
    param([string]$Value)
    try {
        return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
    } catch {
        throw "InstallerArgumentsBase64 must be base64-encoded UTF-8 JSON. Details: $($_.Exception.Message)"
    }
}

function Get-EffectiveInstallerArguments {
    $arguments = @()
    $jsonInputs = @()
    if ($InstallerArgumentsBase64) {
        $jsonInputs += Convert-Base64ToJson -Value $InstallerArgumentsBase64
    }
    if ($InstallerArgumentsJson) {
        $jsonInputs += $InstallerArgumentsJson
    }
    foreach ($jsonInput in $jsonInputs) {
        try {
            $decoded = ConvertFrom-Json -InputObject $jsonInput
        } catch {
            throw "Installer argument JSON must be an array of strings. Details: $($_.Exception.Message)"
        }
        if ($null -ne $decoded) {
            if ($decoded -isnot [array]) {
                throw "Installer argument JSON must be an array of strings."
            }
            foreach ($item in $decoded) {
                if ($null -eq $item) {
                    throw "Installer argument JSON cannot contain null values."
                }
                $arguments += [string]$item
            }
        }
    }
    foreach ($installerArgument in $InstallerArguments) {
        $arguments += [string]$installerArgument
    }
    return $arguments
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
    if ($SkipSelfUpdate) {
        $arguments += "-SkipSelfUpdate"
    }
    if ($SourceArchiveUrl) {
        $arguments += @("-SourceArchiveUrl", (Quote-Argument $SourceArchiveUrl))
    }
    if ($script:EffectiveInstallerArguments.Count -gt 0) {
        $arguments += @("-InstallerArgumentsBase64", (Convert-ArgumentsToBase64 -Arguments $script:EffectiveInstallerArguments))
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

function Remove-SafeTempDirectory {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) {
        return
    }

    $resolvedPath = Resolve-FullPath $Path
    $resolvedTemp = Resolve-FullPath ([IO.Path]::GetTempPath())
    $leafName = Split-Path -Leaf $resolvedPath
    if ($resolvedPath.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and $leafName.StartsWith("gatewatch-self-update-")) {
        Remove-Item -LiteralPath $resolvedPath -Recurse -Force
    }
}

function Copy-SourceOverlay {
    param(
        [string]$SourceRoot,
        [string]$TargetRoot,
        [string]$FailureMessage
    )

    $sourceFull = Resolve-FullPath $SourceRoot
    $targetFull = Resolve-FullPath $TargetRoot
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
        throw "$FailureMessage Robocopy exit code: $exitCode"
    }
}

function Get-ExpandedArchiveRoot {
    param([string]$TempRoot)

    $candidate = Get-ChildItem -LiteralPath $TempRoot -Directory |
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

function Sync-DownloadedSourceFromGitHub {
    param(
        [string]$SourceRoot,
        [string]$ArchiveUrl
    )

    if (Test-Path -LiteralPath (Join-Path $SourceRoot ".git") -PathType Container) {
        Write-Info "Source folder is a Git checkout. Using the checked-out files without archive refresh."
        return
    }

    Assert-HttpsUrl -Url $ArchiveUrl -Description "Gatewatch source archive URL"

    Write-Step "Refresh downloaded files from public GitHub"
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("gatewatch-self-update-" + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
        $zipPath = Join-Path $tempRoot "Gatewatch-main.zip"
        try {
            Write-Host "> Invoke-WebRequest $ArchiveUrl"
            Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zipPath
            Expand-Archive -LiteralPath $zipPath -DestinationPath $tempRoot -Force
            $archiveRoot = Get-ExpandedArchiveRoot -TempRoot $tempRoot
            Assert-SourceFolder -SourceRoot $archiveRoot
            Copy-SourceOverlay -SourceRoot $archiveRoot -TargetRoot $SourceRoot -FailureMessage "Refresh from GitHub failed."
            Write-Info "Downloaded folder is refreshed from the public Gatewatch repository."
        } catch {
            throw "Could not refresh the downloaded Gatewatch folder from GitHub. Confirm this VM can reach $ArchiveUrl, or rerun with -SkipSelfUpdate only if this folder already contains the approved release. Details: $($_.Exception.Message)"
        }
    } finally {
        Remove-SafeTempDirectory -Path $tempRoot
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

    Copy-SourceOverlay -SourceRoot $sourceFull -TargetRoot $targetFull -FailureMessage "Copy to install folder failed."
}

if (-not $InstallRoot) {
    $InstallRoot = Get-DefaultInstallRoot
}
$InstallRoot = Resolve-FullPath $InstallRoot
$sourceRoot = Resolve-FullPath $PSScriptRoot
$script:EffectiveInstallerArguments = @(Get-EffectiveInstallerArguments)

if (-not $NoElevate -and -not (Test-IsAdministrator)) {
    Write-Host "Gatewatch deployment needs an elevated PowerShell window for install-folder ACLs, Docker, and optional firewall/task setup."
    Write-Host "Approving the prompt will open the real deployment window."
    Restart-Elevated
    return
}

Write-Step "Validate downloaded Gatewatch folder"
if (-not $SkipSelfUpdate) {
    Sync-DownloadedSourceFromGitHub -SourceRoot $sourceRoot -ArchiveUrl $SourceArchiveUrl
}
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
) + $script:EffectiveInstallerArguments

Write-Host "> powershell.exe -File $installerPath"
$processArguments = $arguments | ForEach-Object { Quote-Argument ([string]$_) }
$installerProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $processArguments -NoNewWindow -Wait -PassThru
$installerExitCode = $installerProcess.ExitCode
if ($installerExitCode -ne 0) {
    throw "Gatewatch production installer failed. Exit code: $installerExitCode"
}

$global:LASTEXITCODE = 0
