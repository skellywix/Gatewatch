# Gatewatch

Gatewatch is a simple internal employee tracker for small IT and HR workflows.

It keeps the core spreadsheet job, but gives it a cleaner app surface:

- Create an employee.
- Track the employee and Key Fob ID in SQLite.
- Edit employee details.
- Delete employee records when they should be removed.
- Track the normal access handoff with step buttons: request received, manager approved, IT provisioned, employee notified.
- Let non-admin users request changes to existing employee records for Domain Admin approval.
- Search the roster and export the recent activity log.
- Optionally sign in with Microsoft Entra ID and sync users from Microsoft Graph so employee records populate active or disabled status from the directory.
- Give Domain Admins a Configuration tab for host, port, database path, Microsoft SSO, Graph, token status, and blocked-binding checks.
- Give Domain Admins a Logs tab for service health, storage, SQLite, Microsoft SSO, recent audit events, and change-request diagnostics.

The current UI uses a dark behavioral-security console style: a signal-first overview, graphite panels, cyan and green operational states, amber review states, and red critical states. It keeps all behavior on the existing static frontend and does not use third-party visual assets or frontend dependencies.

The app is built for Ubuntu LTS and uses only the Python standard library. There are no Python packages to install.

## Run Locally

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8087
```

The default SQLite database is:

```text
data/gatewatch.db
```

Optional environment variables:

```bash
export GATEWATCH_HOST=127.0.0.1
export GATEWATCH_PORT=8087
export GATEWATCH_DB=/path/to/gatewatch.db
export GATEWATCH_CONFIG_FILE=/path/to/gatewatch.env
python3 app.py
```

Optional Microsoft Entra ID settings:

```bash
export GATEWATCH_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export GATEWATCH_ENTRA_TENANT_ID="00000000-0000-0000-0000-000000000000"
export GATEWATCH_ENTRA_CLIENT_ID="00000000-0000-0000-0000-000000000000"
export GATEWATCH_ENTRA_CLIENT_SECRET="paste-client-secret-here"
export GATEWATCH_ENTRA_REDIRECT_URI="http://127.0.0.1:8087/auth/entra/callback"
export GATEWATCH_ADMIN_GROUP_CANONICAL="gcefcu.org/Users/Domain Admins"
```

The Entra app registration redirect URI must match `GATEWATCH_ENTRA_REDIRECT_URI`. Gatewatch checks the signed-in user's transitive group membership and only allows members of `GATEWATCH_ADMIN_GROUP_CANONICAL` to approve requested edits, directly edit existing employees, delete employees, run directory sync, or open the Logs and Configuration tabs. Non-admin users can still create new access-request records and submit requested edits for approval. For directory sync, grant the app registration Microsoft Graph application permission to read users, such as `User.Read.All`, and grant admin consent.

The Logs tab shows redacted diagnostics for troubleshooting. The Configuration tab saves Domain Admin-entered Entra/AD settings to the server env file, reloads the saved values for verification, and exports a copy-ready environment template. Neither tab echoes raw session secrets or Entra client secrets back to the browser.

By default, Gatewatch refuses to bind local unauthenticated HTTP to non-loopback addresses. If you are putting it behind a protected internal reverse proxy, set:

```bash
export GATEWATCH_HOST=0.0.0.0
export GATEWATCH_ALLOW_INSECURE_NETWORK=1
```

## One-Line Ubuntu Install

On Ubuntu LTS, paste this in the terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash
```

The installer:

- Prompts for install directory, data directory, service name, service user, host, port, and whether to start the service.
- Downloads the latest Gatewatch source from GitHub when it is not already running from a local checkout.
- Verifies Python 3 is available, installing it with `apt-get` when needed.
- Installs `ca-certificates`, `tar`, and `curl` when needed.
- Copies the app into `/opt/gatewatch`.
- Stores SQLite data in `/var/lib/gatewatch/gatewatch.db`.
- Creates `/etc/gatewatch/gatewatch.env`; the Domain Admin Configuration tab saves verified Entra/AD settings back to this file.
- Can prompt for Microsoft Entra tenant ID, client ID, client secret, and redirect URI.
- Installs and starts a locked-down `gatewatch.service` systemd unit.
- Checks `/healthz` before declaring success.

Useful commands:

```bash
systemctl status gatewatch.service
journalctl -u gatewatch.service -f
systemctl restart gatewatch.service
```

Install options:

```bash
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- --yes
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- --port 8090
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- --host 0.0.0.0 --allow-network
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- --entra-tenant-id TENANT --entra-client-id CLIENT --entra-client-secret SECRET --entra-redirect-uri http://127.0.0.1:8087/auth/entra/callback
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- --admin-group-canonical "gcefcu.org/Users/Domain Admins"
```

If you already cloned the repository, this still works from the repository root:

```bash
sudo bash scripts/install-ubuntu.sh
sudo bash scripts/install-ubuntu.sh --port 8090
sudo bash scripts/install-ubuntu.sh --install-dir /srv/gatewatch --data-dir /srv/gatewatch-data
```

Keep the default `127.0.0.1` bind unless a reverse proxy or SSH tunnel is protecting access.

For release and operator validation steps, use [docs/ROLLOUT.md](docs/ROLLOUT.md).

## Docker

Docker is optional. It is useful for smoke testing the Linux runtime shape:

```bash
docker build -t gatewatch-ci .
docker run --rm -p 127.0.0.1:8087:8087 gatewatch-ci
```

For a repeatable remote container rollout, use the SSH deploy helper from a machine that can reach the Docker host:

```bash
bash scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP
```

To rebuild from GitHub `main` and remove only the named Gatewatch Docker data volume:

```bash
bash scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP --reset-data
```

The helper builds the image from the GitHub source archive, replaces the named container, optionally removes the named volume, starts the app with a read-only root filesystem, and checks `/healthz`.

### Trusted-Proxy Browser Lab

`docker/full-test` runs Gatewatch in `trusted_proxy` mode behind a lightweight authenticated test proxy. Use it when you want to exercise the app through a browser SSO path instead of sending raw identity headers to the app:

```bash
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml up -d --build app proxy
```

Open:

```text
http://127.0.0.1:18107
```

The proxy maps the test user `Grace Admin <grace.admin@gatewatch.test>` into `GATEWATCH\Gatewatch-Admins`, which Gatewatch treats as the configured Domain Admin group. Run the end-to-end smoke with:

```bash
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml run --rm browser-smoke
```

That smoke reaches only the proxy URL, confirms the browser session is `trusted_proxy`, verifies Domain Admin permissions, creates and deletes an employee, and checks the audit actor. Reset the lab with:

```bash
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml down -v
```

## Test

```bash
python3 scripts/verify.py
```

Run the Docker build check too:

```bash
python3 scripts/verify.py --docker
```

Run the trusted-proxy browser lab smoke too:

```bash
python3 scripts/verify.py --docker-full-test
```

The verification runner compiles Python, runs the unit and HTTP smoke tests, checks the frontend JavaScript syntax when Node is available, runs the frontend monitor regression when Node is available, optionally builds the Docker image, and can optionally start the full-test proxy lab to prove browser SSO role mapping end to end.

## Security Notes

- Gatewatch is intentionally simple. Microsoft Entra ID sign-in is available when configured; editing existing employees, deleting employees, directory sync, and the Logs and Configuration tabs require membership in the configured admin group.
- Non-admin edits to existing employees are stored as pending change requests until a configured admin approves or rejects them.
- Employee creation and read-only access still assume the app is protected by loopback, a tunnel, VPN, or an authenticated reverse proxy.
- Keep it on `127.0.0.1` or place it behind an authenticated internal reverse proxy.
- Treat the SQLite database as company data.
- Treat `/etc/gatewatch/gatewatch.env` as sensitive because it can contain the Entra client secret and cookie signing secret.
- The systemd service runs as a dedicated `gatewatch` user and only writes to the configured data directory plus `/etc/gatewatch` for admin-approved configuration saves.
