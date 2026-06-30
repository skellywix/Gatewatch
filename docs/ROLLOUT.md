# Gatewatch Rollout Runbook

Use this runbook for every Gatewatch release. It keeps the release small, testable, and reversible.

## 1. Preflight

From the repository root:

```powershell
git status --short --branch
python scripts\verify.py
```

If Docker is available, also run:

```powershell
python scripts\verify.py --docker
```

When trusted-proxy browser SSO is in scope, run the full-test proxy smoke:

```powershell
python scripts\verify.py --docker-full-test
```

Do not continue if the worktree has unrelated changes or verification fails.

## 2. Local Functional Rehearsal

Start a local rehearsal server with an isolated database:

```powershell
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$env:GATEWATCH_HOST = '127.0.0.1'
$env:GATEWATCH_PORT = '8087'
$env:GATEWATCH_DB = "$PWD\output\rollout-$stamp.db"
$env:GATEWATCH_SESSION_SECRET = 'replace-with-local-test-secret'
$env:GATEWATCH_ENTRA_TENANT_ID = 'example-tenant'
$env:GATEWATCH_ENTRA_CLIENT_ID = 'example-client'
$env:GATEWATCH_ENTRA_CLIENT_SECRET = 'example-client-secret'
$env:GATEWATCH_ENTRA_REDIRECT_URI = 'http://127.0.0.1:8087/auth/entra/callback'
$env:GATEWATCH_ADMIN_GROUP_CANONICAL = 'gcefcu.org/Users/Domain Admins'
python app.py
```

Open:

```text
http://127.0.0.1:8087
```

Validate these flows before release:

- User creates a new access-request employee record with a Key Fob ID.
- User selects an existing employee, edits request/details, and receives a pending change-request confirmation.
- User cannot delete employees, sync Entra, or open Configuration.
- Domain Admin can approve a pending change request and the employee fields update.
- Domain Admin can reject a pending change request and the employee fields do not update.
- Domain Admin can directly edit an existing employee.
- Domain Admin can delete an employee and the Activity Log records who deleted it.
- Configuration tab is visible only to Domain Admins.
- Configuration tab masks session and Entra client secrets.
- Activity Log export opens as CSV.
- Browser Back moves between Overview, Users, Activity Log, and Configuration tabs.
- The dark access-defense console renders at desktop, tablet, and mobile widths without horizontal overflow.
- Overview search, signal filters, selected signal state, and the inspector stay wired to the saved SQLite records.

## 3. Ubuntu Rollout

On the Ubuntu host:

```bash
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- \
  --host 127.0.0.1 \
  --port 8087 \
  --admin-group-canonical "gcefcu.org/Users/Domain Admins"
```

When Microsoft SSO and Graph sync are ready, include the Entra settings:

```bash
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- \
  --host 127.0.0.1 \
  --port 8087 \
  --entra-tenant-id TENANT_ID \
  --entra-client-id CLIENT_ID \
  --entra-client-secret CLIENT_SECRET \
  --entra-redirect-uri http://127.0.0.1:8087/auth/entra/callback \
  --admin-group-canonical "gcefcu.org/Users/Domain Admins"
```

Use `--host 0.0.0.0 --allow-network` only when a trusted reverse proxy, VPN, or tunnel protects access.

## 4. Remote Container Rollout

Use this path when Gatewatch is running as a Docker container on a remote Linux host instead of a systemd service:

```bash
bash scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP
```

To rebuild from GitHub `main` and intentionally clear old Gatewatch container data:

```bash
bash scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP --reset-data
```

The reset deletes only the configured Gatewatch container and Docker volume. Override those names when needed:

```bash
bash scripts/deploy-container.sh \
  --target user@host \
  --bind-ip HOST_LAN_IP \
  --container-name gatewatch-test \
  --volume-name gatewatch-test-data \
  --image-name gatewatch:test \
  --admin-group-canonical "gcefcu.org/Users/Domain Admins"
```

Use the Microsoft Entra options only when the app registration and secret are ready:

```bash
export GATEWATCH_ENTRA_CLIENT_SECRET="paste-client-secret-here"
bash scripts/deploy-container.sh \
  --target user@host \
  --bind-ip HOST_LAN_IP \
  --entra-tenant-id TENANT_ID \
  --entra-client-id CLIENT_ID \
  --entra-redirect-uri http://HOST_LAN_IP:8087/auth/entra/callback \
  --admin-group-canonical "gcefcu.org/Users/Domain Admins"
unset GATEWATCH_ENTRA_CLIENT_SECRET
```

After the helper reports success, validate:

```bash
curl -fsS http://HOST_LAN_IP:8087/healthz
docker ps --filter name=gatewatch-test
```

## 5. Trusted-Proxy Browser Lab

Use this path to validate Gatewatch behind an authenticated proxy before wiring a real SSO gateway:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml up -d --build app proxy
```

Open:

```text
http://127.0.0.1:18107
```

Then run the automated proof:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml run --rm browser-smoke
```

The smoke confirms the browser-facing proxy maps `Grace Admin <grace.admin@gatewatch.test>` into Domain Admin permissions, then creates and deletes an employee through the proxied UI/API path and verifies the audit actor.

Reset only the lab containers and volume:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml down -v
```

## 6. Post-Rollout Verification

On the Ubuntu host:

```bash
systemctl status gatewatch.service --no-pager
journalctl -u gatewatch.service -n 80 --no-pager
curl -fsS http://127.0.0.1:8087/healthz
```

Then repeat the functional rehearsal against the deployed URL:

- Create employee.
- User change request.
- Admin approve.
- Admin reject.
- Admin direct edit.
- Admin delete.
- Activity Log and CSV export.
- Configuration visibility and masked secrets.

## 7. Rollback

If rollout fails after service start:

```bash
systemctl stop gatewatch.service
cp /var/lib/gatewatch/gatewatch.db /var/lib/gatewatch/gatewatch.db.rollback-copy
journalctl -u gatewatch.service -n 120 --no-pager
```

Restore the previous `/opt/gatewatch` app files from backup or redeploy the previous GitHub release, then:

```bash
systemctl daemon-reload
systemctl restart gatewatch.service
curl -fsS http://127.0.0.1:8087/healthz
```

Keep `/etc/gatewatch/gatewatch.env` private. It can contain the cookie signing secret and Entra client secret.
