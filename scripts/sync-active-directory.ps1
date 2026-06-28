[CmdletBinding()]
param(
    [string]$GatewatchUrl = $env:GATEWATCH_URL,
    [string]$SourceName = $env:GATEWATCH_AD_SOURCE,
    [string]$DomainController = $env:GATEWATCH_AD_SERVER,
    [string]$SearchBase = $env:GATEWATCH_AD_SEARCH_BASE,
    [string]$Filter = "*",
    [string]$LdapFilter = $env:GATEWATCH_AD_LDAP_FILTER,
    [string]$Actor = $env:GATEWATCH_SYNC_ACTOR,
    [string]$RemoteUser = $env:GATEWATCH_SYNC_REMOTE_USER,
    [string]$RemoteEmail = $env:GATEWATCH_SYNC_REMOTE_EMAIL,
    [string]$RemoteName = $env:GATEWATCH_SYNC_REMOTE_NAME,
    [string]$RemoteGroups = $env:GATEWATCH_SYNC_REMOTE_GROUPS,
    [string]$ProxySecret = $env:ACCESS_REGISTER_PROXY_SECRET,
    [switch]$UseDefaultCredentialsForSso,
    [switch]$RouteDisabledAccess,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

if (-not $GatewatchUrl) {
    throw "GatewatchUrl is required. Set -GatewatchUrl or GATEWATCH_URL."
}

if (-not $SourceName) {
    $SourceName = "Active Directory service account export"
}

if (-not $Actor) {
    $Actor = "$env:USERDOMAIN\$env:USERNAME"
}

if ($RemoteUser -and -not $ProxySecret) {
    throw "Remote header mode requires ACCESS_REGISTER_PROXY_SECRET or -ProxySecret so clients cannot spoof trusted identity headers."
}

if ($RemoteUser -and -not $RemoteGroups) {
    throw "Remote header mode requires -RemoteGroups or GATEWATCH_SYNC_REMOTE_GROUPS with the Gatewatch Admin group mapping."
}

if (-not $RemoteUser -and -not $UseDefaultCredentialsForSso) {
    throw "Use -UseDefaultCredentialsForSso when posting through the AD-authenticated reverse proxy, or set -RemoteUser, -RemoteGroups, and -ProxySecret for a controlled direct service-account job."
}

Import-Module ActiveDirectory -ErrorAction Stop

$properties = @(
    "EmployeeID",
    "Mail",
    "Department",
    "Office",
    "Manager",
    "Enabled",
    "ObjectGUID",
    "UserPrincipalName",
    "SamAccountName",
    "DistinguishedName",
    "LastLogonDate"
)

$adArgs = @{
    Properties = $properties
}

if ($DomainController) {
    $adArgs.Server = $DomainController
}

if ($SearchBase) {
    $adArgs.SearchBase = $SearchBase
}

if ($LdapFilter) {
    $users = @(Get-ADUser -LDAPFilter $LdapFilter @adArgs)
} else {
    $users = @(Get-ADUser -Filter $Filter @adArgs)
}

if ($users.Count -eq 0) {
    throw "Active Directory query returned no users."
}

$rows = @(
    $users |
        Sort-Object SamAccountName |
        ForEach-Object {
            $lastLogon = ""
            if ($_.LastLogonDate) {
                $lastLogon = $_.LastLogonDate.ToUniversalTime().ToString("o")
            }

            $enabled = ""
            if ($null -ne $_.Enabled) {
                $enabled = if ($_.Enabled) { "TRUE" } else { "FALSE" }
            }

            [pscustomobject][ordered]@{
                EmployeeID = $_.EmployeeID
                Name = $_.Name
                Mail = $_.Mail
                Department = $_.Department
                Office = $_.Office
                Manager = $_.Manager
                Enabled = $enabled
                ObjectGUID = if ($_.ObjectGUID) { $_.ObjectGUID.Guid } else { "" }
                UserPrincipalName = $_.UserPrincipalName
                SamAccountName = $_.SamAccountName
                DistinguishedName = $_.DistinguishedName
                LastLogonDate = $lastLogon
            }
        }
)

$directoryText = ($rows | ConvertTo-Csv -NoTypeInformation) -join [Environment]::NewLine

$headers = @{
    Accept = "application/json"
    "X-Requested-With" = "XMLHttpRequest"
}

if ($ProxySecret) {
    $headers["X-Access-Register-Proxy-Secret"] = $ProxySecret
}

if ($RemoteUser) {
    $headers["X-Remote-User"] = $RemoteUser
    $headers["X-Remote-Name"] = if ($RemoteName) { $RemoteName } else { $Actor }
    $headers["X-Remote-Groups"] = $RemoteGroups
    if ($RemoteEmail) {
        $headers["X-Remote-Email"] = $RemoteEmail
    }
} else {
    $headers["X-App-Actor"] = $Actor
}

function Invoke-GatewatchPost {
    param(
        [string]$Path,
        [hashtable]$Payload,
        [int]$TimeoutSec = 60
    )

    $request = @{
        Uri = "$($GatewatchUrl.TrimEnd('/'))$Path"
        Method = "Post"
        ContentType = "application/json"
        Headers = $headers
        Body = ($Payload | ConvertTo-Json -Depth 8)
        TimeoutSec = $TimeoutSec
    }

    if ($UseDefaultCredentialsForSso) {
        $request.UseDefaultCredentials = $true
    }

    return Invoke-RestMethod @request
}

$syncResponse = Invoke-GatewatchPost -Path "/api/ad/sync" -Payload @{
    source_name = $SourceName
    format = "csv"
    directory_text = $directoryText
} -TimeoutSec 120

$adSyncRun = $syncResponse.adSyncRun
if (-not $adSyncRun) {
    throw "Gatewatch AD sync response did not include adSyncRun."
}

if ($adSyncRun.error_rows -gt 0) {
    throw "Gatewatch AD sync completed with $($adSyncRun.error_rows) row error(s)."
}

$routeResponse = $null
if ($RouteDisabledAccess) {
    $routeResponse = Invoke-GatewatchPost -Path "/api/disabled-access/route-removal" -Payload @{} -TimeoutSec 60
}

$result = [ordered]@{
    gatewatch_url = $GatewatchUrl
    source_name = $SourceName
    actor = $Actor
    ad_rows_exported = $rows.Count
    total_rows = $adSyncRun.total_rows
    created_users = $adSyncRun.created_users
    updated_users = $adSyncRun.updated_users
    disabled_users = $adSyncRun.disabled_users
    error_rows = $adSyncRun.error_rows
    routed_disabled_access = if ($routeResponse) { $routeResponse.result.routed } else { $null }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
    return
}

Write-Host "Gatewatch production AD sync completed"
Write-Host "Gatewatch: $($result.gatewatch_url)"
Write-Host "AD rows exported: $($result.ad_rows_exported)"
Write-Host "Rows synced: $($result.total_rows)"
Write-Host "Created users: $($result.created_users)"
Write-Host "Updated users: $($result.updated_users)"
Write-Host "Disabled users: $($result.disabled_users)"
Write-Host "Error rows: $($result.error_rows)"
if ($RouteDisabledAccess) {
    Write-Host "Routed disabled access: $($result.routed_disabled_access)"
}
