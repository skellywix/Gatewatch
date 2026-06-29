# Gatewatch Full Docker AD Sync Test

This profile runs a production-shaped Gatewatch test in Docker:

- Samba Active Directory domain controller for `GATEWATCH.TEST`.
- Production-style AD role group:
  - `GATEWATCH\AccessRegister-Admins`
- Authenticated accounts outside that group exercise the User role.
- Dedicated AD sync service account: `GATEWATCH\svc.gatewatch.adsync`.
- Gatewatch app in `trusted_proxy` mode.
- On-demand sync runner that binds to AD over LDAPS as the service account, exports employee users, and posts to Gatewatch through trusted proxy headers.

The test AD password values are local Docker lab secrets only. Do not reuse them in production.

## Start the Full Test Lab

From the repository root:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml up -d --build ad app
```

Open the app at:

```text
http://127.0.0.1:18107
```

The app is in `trusted_proxy` mode, so direct browser access without a proxy identity header will show an authentication error. Use the API checks below or put a test proxy in front if you need browser SSO behavior.

## Run the AD Sync

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml run --rm ad-sync
```

Expected first sync result:

```json
{
  "ldap_rows": 7,
  "adSyncRun": {
    "source_name": "Docker LDAPS service-account sync",
    "created_users": 7,
    "disabled_users": 1,
    "error_rows": 0
  }
}
```

Subsequent runs should update the same seven users rather than creating duplicates.

## Trusted Proxy API Check

```powershell
$Headers = @{
  "X-Access-Register-Proxy-Secret" = "GatewatchFullTestProxySecret123!"
  "X-Remote-User" = "GATEWATCH\gw.admin"
  "X-Remote-Name" = "Grace Admin"
  "X-Remote-Groups" = "GATEWATCH\AccessRegister-Admins"
}
Invoke-RestMethod "http://127.0.0.1:18107/api/summary" -Headers $Headers
```

## Verify AD Accounts

```powershell
docker exec gatewatch-full-ad samba-tool group listmembers "AccessRegister-Admins"
docker exec gatewatch-full-ad samba-tool user show svc.gatewatch.adsync
docker exec gatewatch-full-ad sh -c "LDAPTLS_REQCERT=never ldapsearch -LLL -H ldaps://127.0.0.1 -D 'svc.gatewatch.adsync@GATEWATCH.TEST' -w 'GatewatchSync123!' -b 'DC=gatewatch,DC=test' '(&(objectClass=user)(employeeID=*))' sAMAccountName employeeID userAccountControl"
```

## Reset the Full Test Lab

This deletes only the full-test lab containers and volumes:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml down -v
```
