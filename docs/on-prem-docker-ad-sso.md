# On-Prem Docker AD SSO

Last reviewed: 2026-06-28

Eric Gatewatch can run in Docker on an internal server, but Active Directory SSO, TLS, and header trust should live at the reverse proxy boundary. The Python app now supports a `trusted_proxy` mode for that deployment shape.

## Target Architecture

```text
Domain browser
  -> https://gatewatch.company.local
  -> reverse proxy with Kerberos/Negotiate or AD FS/OIDC
  -> loopback or firewall-restricted Docker app port
  -> Eric Gatewatch container on :8087
  -> mounted /data/access_register.db
```

The reverse proxy must authenticate the user, remove any incoming identity headers from the client, then inject trusted identity headers for the app.

## App Container on vSphere

For Docker on a vSphere VM, use the Compose profile in `docker/vsphere/`:

```powershell
Copy-Item docker/vsphere/.env.example docker/vsphere/.env
notepad docker/vsphere/.env
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml up -d --build
```

The profile runs the app in `trusted_proxy` mode, stores SQLite in the `gatewatch-data` Docker volume, drops Linux capabilities, uses a read-only container filesystem, and binds the app to `127.0.0.1:8087` by default.

Use this direct `docker run` shape only for one-off troubleshooting:

```powershell
docker run --rm `
  --name eric-gatewatch `
  --network gatewatch-internal `
  -e ACCESS_REGISTER_AUTH_MODE=trusted_proxy `
  -e ACCESS_REGISTER_PROXY_SECRET="long-random-proxy-only-value" `
  -e ACCESS_REGISTER_ADMIN_GROUPS="DOMAIN\AccessRegister-Admins" `
  -e ACCESS_REGISTER_SUPERVISOR_GROUPS="DOMAIN\AccessRegister-Supervisors" `
  -e ACCESS_REGISTER_DB=/data/access_register.db `
  -v gatewatch-data:/data `
  -p 127.0.0.1:8087:8087 `
  eric-gatewatch:local
```

Set at least `ACCESS_REGISTER_ADMIN_GROUPS` on first trusted-proxy startup. A fresh database has no group mappings yet, so the environment fallback is the bootstrap path for the first Admin user.

For local development only, use the browser role selector. The app blocks local mode on non-loopback binds unless `ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=1` is set for an isolated demo network.

```powershell
docker run --rm -p 8087:8087 `
  -e ACCESS_REGISTER_AUTH_MODE=local `
  -e ACCESS_REGISTER_DB=/data/access_register.db `
  -v gatewatch-dev-data:/data `
  eric-gatewatch:local
```

## Trusted Headers

In `trusted_proxy` mode, every request must include an authenticated user header:

```text
X-Remote-User: avery.morgan@example.local
X-Remote-Email: avery.morgan@example.local
X-Remote-Name: Avery Morgan
X-Remote-Groups: DOMAIN\AccessRegister-Supervisors;DOMAIN\AccessRegister-ReadOnly
```

Supported alternatives include `X-Forwarded-User`, `X-Authenticated-User`, `X-Forwarded-Email`, `X-Remote-Upn`, `X-Remote-Sam`, and `X-Forwarded-Groups`.

The proxy must strip these headers from inbound client requests before adding its own values. Do not publish the app container directly to the LAN in `trusted_proxy` mode.

Set a proxy-only shared header:

```powershell
-e ACCESS_REGISTER_PROXY_SECRET="long-random-value"
```

Then configure the proxy to send:

```text
X-Access-Register-Proxy-Secret: long-random-value
```

## Production AD Sync Job

Create a domain service account or gMSA with read-only access to the user attributes Gatewatch imports. Run the sync job under that account from a domain-joined Windows host with the ActiveDirectory PowerShell module installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/sync-active-directory.ps1 `
  -GatewatchUrl "https://gatewatch.company.local" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -UseDefaultCredentialsForSso
```

When the job calls the same AD-authenticated reverse proxy as users, `-UseDefaultCredentialsForSso` lets the proxy authenticate the service account and inject identity plus group headers. Put the service account in the Gatewatch Admin mapping group or a narrower dedicated group that maps to Admin for imports.

For a controlled server-side job that calls the app directly on an isolated management network, require the proxy secret and provide trusted identity headers:

```powershell
$env:ACCESS_REGISTER_PROXY_SECRET="long-random-value"
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/sync-active-directory.ps1 `
  -GatewatchUrl "http://127.0.0.1:8087" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -RemoteUser "DOMAIN\svc-gatewatch-adsync" `
  -RemoteGroups "DOMAIN\AccessRegister-Admins"
```

Do not expose that direct app port to LAN clients. Browser users should only reach Gatewatch through the authenticated TLS proxy.

## Role Mapping

The app maps AD groups to roles using Security settings in the app or environment variables:

| App role | Security setting | Environment fallback |
| --- | --- | --- |
| Admin | `admin_group` | `ACCESS_REGISTER_ADMIN_GROUPS` |
| Supervisor | `supervisor_group` | `ACCESS_REGISTER_SUPERVISOR_GROUPS` |
| Reviewer | `reviewer_group` | `ACCESS_REGISTER_REVIEWER_GROUPS` |
| HR | `hr_group` | `ACCESS_REGISTER_HR_GROUPS` |
| ReadOnly | `readonly_group` | `ACCESS_REGISTER_READONLY_GROUPS` |

If the user is authenticated but not in a mapped group, the app assigns the Employee role and attempts to link the AD identity to an employee record by email, UPN, SAM account name, or employee ID.

## Role Behavior

- Admin can do everything, including imports, AD sync, auth settings, backups, and audit exports.
- Supervisor can create business categories and resources such as Company Facebook under Social Media, create or approve access requests, certify access, and route removals.
- Employee can view only their own employee record, access records, and requests, and can submit requests only for themself.
- ReadOnly can view operational inventory and audit evidence without writes.

## SSO Requirements

- Create internal DNS for `gatewatch.company.local`.
- Use an internal TLS certificate trusted by domain machines.
- Configure Kerberos SPN for the reverse proxy service account, for example `HTTP/gatewatch.company.local`.
- Configure browsers or GPO so the site is in the intranet SSO zone.
- Keep the app container reachable only from the reverse proxy network.
- Ensure the proxy forwards `X-Forwarded-Proto: https` or equivalent for logging and future secure-cookie work.

## CSRF Boundary

Because AD SSO is automatic in the browser, trusted-proxy mode rejects mutating requests unless they include:

```text
X-Requested-With: XMLHttpRequest
```

The app also rejects mutating requests when browser fetch metadata says the request is cross-site. Keep this control at the app layer even when the reverse proxy also has CSRF or origin checks.

## Backups and Retention

- Store `/data` on protected VM or container storage with OS-level access limited to Gatewatch operators and backup tooling.
- Use Governance backups for application-level restore points. Each successful run enforces the requested retention window for managed backup files under `/data/backups`.
- Keep VM, volume, and off-host backup retention under the organization's retention policy. The in-app retention pass does not replace infrastructure backups or legal hold controls.

## Current Gaps

- The app trusts the proxy header contract. A direct path to the container would allow header spoofing unless network isolation or `ACCESS_REGISTER_PROXY_SECRET` blocks it.
- Supervisor users are scoped to their own employee row and direct reports in trusted-proxy mode. Keep HR or AD manager data accurate before broad Supervisor rollout.
- SQLite remains single-writer local storage. Move to a managed database if multiple app replicas or high availability are required.
