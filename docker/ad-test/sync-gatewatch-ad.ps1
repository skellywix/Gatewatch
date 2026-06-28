[CmdletBinding()]
param(
    [string]$GatewatchUrl = $env:GATEWATCH_TEST_URL,
    [string]$AdContainer = $env:GATEWATCH_AD_CONTAINER,
    [string]$Actor = $env:GATEWATCH_SYNC_ACTOR,
    [string]$SourceName = $env:GATEWATCH_SYNC_SOURCE,
    [switch]$RouteDisabledAccess,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

if (-not $GatewatchUrl) {
    $GatewatchUrl = "http://127.0.0.1:18099"
}

if (-not $AdContainer) {
    $AdContainer = "gatewatch-ad-test"
}

if (-not $Actor) {
    $Actor = "Docker AD Sync"
}

if (-not $SourceName) {
    $SourceName = "Docker Samba AD lab"
}

function Invoke-Docker {
    param([string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }

    return $output
}

function Get-RunningContainerStatus {
    param([string]$ContainerName)

    try {
        return (Invoke-Docker -Arguments @("inspect", "-f", "{{.State.Running}}", $ContainerName) | Select-Object -First 1).Trim()
    } catch {
        throw "Docker container '$ContainerName' was not found or is not inspectable. Start the AD lab before syncing."
    }
}

$running = Get-RunningContainerStatus -ContainerName $AdContainer
if ($running -ne "true") {
    throw "Docker container '$AdContainer' is not running."
}

$healthUrl = "$($GatewatchUrl.TrimEnd('/'))/api/summary"
try {
    $null = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 15
} catch {
    throw "Gatewatch is not reachable at $GatewatchUrl. Start the gatewatch-test container before syncing. $($_.Exception.Message)"
}

$csvLines = Invoke-Docker -Arguments @("exec", $AdContainer, "export-gatewatch-ad")
$csvText = ($csvLines -join [Environment]::NewLine).Trim()
if (-not $csvText -or (($csvText -split "`r?`n").Count -lt 2)) {
    throw "AD export from '$AdContainer' did not return any user rows."
}

$headers = @{
    Accept = "application/json"
    "X-App-Role" = "Admin"
    "X-App-Actor" = $Actor
}

$body = @{
    source_name = $SourceName
    format = "csv"
    directory_text = $csvText
} | ConvertTo-Json -Depth 5

$syncUrl = "$($GatewatchUrl.TrimEnd('/'))/api/ad/sync"
$syncResponse = Invoke-RestMethod -Uri $syncUrl -Method Post -ContentType "application/json" -Headers $headers -Body $body -TimeoutSec 30
$adSyncRun = $syncResponse.adSyncRun
if (-not $adSyncRun) {
    throw "Gatewatch AD sync response did not include adSyncRun."
}

$routeResponse = $null
if ($RouteDisabledAccess) {
    $routeUrl = "$($GatewatchUrl.TrimEnd('/'))/api/disabled-access/route-removal"
    $routeResponse = Invoke-RestMethod -Uri $routeUrl -Method Post -ContentType "application/json" -Headers $headers -Body "{}" -TimeoutSec 30
}

$result = [ordered]@{
    gatewatch_url = $GatewatchUrl
    ad_container = $AdContainer
    source_name = $SourceName
    actor = $Actor
    total_rows = $adSyncRun.total_rows
    created_users = $adSyncRun.created_users
    updated_users = $adSyncRun.updated_users
    disabled_users = $adSyncRun.disabled_users
    error_rows = $adSyncRun.error_rows
    routed_disabled_access = if ($routeResponse) { $routeResponse.result.routed } else { $null }
}

if ($result.error_rows -gt 0) {
    throw "Gatewatch AD sync completed with $($result.error_rows) row error(s)."
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
    return
}

Write-Host "Gatewatch AD sync completed"
Write-Host "Gatewatch: $($result.gatewatch_url)"
Write-Host "AD container: $($result.ad_container)"
Write-Host "Rows: $($result.total_rows)"
Write-Host "Created users: $($result.created_users)"
Write-Host "Updated users: $($result.updated_users)"
Write-Host "Disabled users: $($result.disabled_users)"
Write-Host "Error rows: $($result.error_rows)"
if ($RouteDisabledAccess) {
    Write-Host "Routed disabled access: $($result.routed_disabled_access)"
}
