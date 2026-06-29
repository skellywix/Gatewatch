# Gatewatch on Docker for vSphere

This profile runs the Gatewatch app container on a single vSphere VM with persistent SQLite storage and trusted-proxy authentication.

## Target Shape

```text
LAN browser
  -> https://gatewatch.company.local
  -> AD-authenticated TLS reverse proxy
  -> 127.0.0.1:8087 on the vSphere VM, or a firewall-restricted app port
  -> gatewatch-app container
  -> gatewatch-data Docker volume
```

Do not expose the app container directly to the LAN. The browser-facing endpoint should be the reverse proxy on TCP 443.

## First Run

From the repository root on the vSphere VM:

```powershell
Copy-Item docker/vsphere/.env.example docker/vsphere/.env
notepad docker/vsphere/.env
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml up -d --build
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml ps
```

Set these values before the first start:

- `ACCESS_REGISTER_PROXY_SECRET`: long random value known only to the proxy and app.
- `ACCESS_REGISTER_ADMIN_GROUPS`: AD group that should bootstrap Admin access.
- `GATEWATCH_BIND_ADDRESS`: keep `127.0.0.1` unless an external proxy host needs access and the VM firewall restricts the port to that proxy.

Generate a proxy secret on Windows PowerShell:

```powershell
$Bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
[Convert]::ToBase64String($Bytes)
```

## Local Health Check

Run from the vSphere VM:

```powershell
$EnvFile = Get-Content docker/vsphere/.env | Where-Object { $_ -and -not $_.StartsWith("#") }
$Config = @{}
$EnvFile | ForEach-Object {
  $parts = $_.Split("=", 2)
  $Config[$parts[0]] = $parts[1]
}
$Port = if ($Config.GATEWATCH_APP_PORT) { $Config.GATEWATCH_APP_PORT } else { "8087" }
Invoke-RestMethod "http://127.0.0.1:$Port/healthz"
```

## Reverse Proxy Contract

The reverse proxy must:

- Authenticate users with AD SSO, AD FS, Entra ID, or another approved identity provider.
- Strip inbound client-supplied identity headers.
- Inject `X-Remote-User`, `X-Remote-Email`, `X-Remote-Name`, and `X-Remote-Groups`.
- Inject `X-Access-Register-Proxy-Secret` with the value from `.env`.
- Terminate TLS with an internal certificate trusted by domain machines.
- Be the only LAN-facing path to Gatewatch.

## Operations

```powershell
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml logs --tail 100 app
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml restart app
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml pull
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml up -d --build
```

Back up the VM and the `gatewatch-data` Docker volume. Gatewatch also supports an in-app SQLite backup from Governance; successful in-app backup runs prune expired managed backup files under `/data/backups` according to the selected retention window. Use infrastructure backup retention for off-host copies and any legal hold requirements.

## AD Sync

Run production directory sync as a scheduled task under a domain service account or gMSA:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/sync-active-directory.ps1 `
  -GatewatchUrl "https://gatewatch.company.local" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -UseDefaultCredentialsForSso
```

If the sync job runs on the same VM and calls the local app port directly, keep `GATEWATCH_BIND_ADDRESS=127.0.0.1` and pass trusted service-account headers plus the proxy secret:

```powershell
$env:ACCESS_REGISTER_PROXY_SECRET="<same value as docker/vsphere/.env>"
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/sync-active-directory.ps1 `
  -GatewatchUrl "http://127.0.0.1:8087" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -RemoteUser "DOMAIN\svc-gatewatch-adsync" `
  -RemoteGroups "DOMAIN\AccessRegister-Admins"
```
