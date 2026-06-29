# Gatewatch

Gatewatch is a simple internal employee tracker for small IT and HR workflows.

It keeps the core spreadsheet job, but gives it a cleaner app surface:

- Create an employee.
- Track the employee in SQLite.
- Edit employee details.
- Delete employee records when they should be removed.
- Track the normal access handoff with step buttons: request received, manager approved, IT provisioned, employee notified.
- Search the roster and export the recent activity log.

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
python3 app.py
```

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
- Creates `/etc/gatewatch/gatewatch.env`.
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
```

If you already cloned the repository, this still works from the repository root:

```bash
sudo bash scripts/install-ubuntu.sh
sudo bash scripts/install-ubuntu.sh --port 8090
sudo bash scripts/install-ubuntu.sh --install-dir /srv/gatewatch --data-dir /srv/gatewatch-data
```

Keep the default `127.0.0.1` bind unless a reverse proxy or SSH tunnel is protecting access.

## Docker

Docker is optional. It is useful for smoke testing the Linux runtime shape:

```bash
docker build -t gatewatch-ci .
docker run --rm -p 127.0.0.1:8087:8087 gatewatch-ci
```

## Test

```bash
python3 scripts/verify.py
```

Run the Docker build check too:

```bash
python3 scripts/verify.py --docker
```

The verification runner compiles Python, runs the unit and HTTP smoke tests, checks the frontend JavaScript syntax when Node is available, and optionally builds the Docker image.

## Security Notes

- Gatewatch is intentionally simple and does not include enterprise authentication.
- Keep it on `127.0.0.1` or place it behind an authenticated internal reverse proxy.
- Treat the SQLite database as company data.
- The systemd service runs as a dedicated `gatewatch` user and only writes to the configured data directory.
