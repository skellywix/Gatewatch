# Gatewatch Codebase Notes

Last reviewed: 2026-06-30.

## Project Shape

Gatewatch is a small standard-library Python web app for tracking employees, SQLite-backed access requests, and the request handoff steps: request received, manager approved, IT provisioned, and employee notified.

Repository layout:

- `app.py` is the backend, HTTP server, SQLite store, auth/session handling, Microsoft Entra integration, admin configuration, diagnostics, and route dispatch.
- `web/index.html`, `web/app.js`, and `web/styles.css` are the browser UI. The first screen is the working employee tracker, not a landing page.
- `tests/` contains standard-library `unittest` coverage for the store, HTTP routes, auth/authorization, config/diagnostics, deployment scripts, Docker metadata, and the verification runner.
- `scripts/verify.py` is the local and CI verification checklist.
- `scripts/install-ubuntu.sh` installs Gatewatch as a locked-down Ubuntu systemd service.
- `scripts/deploy-container.sh` deploys a locked-down Alpine-based Docker container to a remote Linux Docker host over SSH.
- `docker/full-test/` contains an optional trusted-proxy Docker Compose lab with a tiny proxy and browser-style SSO smoke test.
- `docs/ROLLOUT.md` is the release and operator validation runbook.

There are no Python package dependency manifests. The app uses only the Python standard library. Docker and Node are optional local validation tools.

## Setup And Commands

Install dependencies:

- Python 3.10 or newer is required.
- No Python packages need to be installed.
- Node is optional and only used for `node --check web/app.js`.
- Docker is optional and only used for image and trusted-proxy lab checks.
- On Ubuntu installs, `scripts/install-ubuntu.sh` ensures `ca-certificates`, `tar`, `curl`, and `python3` are present.

Run locally:

```bash
python3 app.py
```

Open `http://127.0.0.1:8087`. The default database is `data/gatewatch.db`.

Useful environment variables:

```bash
export GATEWATCH_HOST=127.0.0.1
export GATEWATCH_PORT=8087
export GATEWATCH_DB=/path/to/gatewatch.db
export GATEWATCH_CONFIG_FILE=/path/to/gatewatch.env
python3 app.py
```

Test and validation:

```bash
python3 scripts/verify.py
python3 scripts/verify.py --docker
python3 scripts/verify.py --docker-full-test
python3 scripts/verify.py --repeat 2
python3 scripts/verify.py --list
```

`scripts/verify.py` compiles Python, runs `unittest` discovery, checks `web/app.js` syntax when Node is installed, optionally builds the Docker image, and optionally runs the trusted-proxy browser SSO lab.

Lint, typecheck, and build:

- There is no dedicated Python linter or typechecker configured.
- The nearest syntax/lint checks are `python -m compileall ...` and `node --check web/app.js`, both run through `scripts/verify.py`.
- The production build check is `python3 scripts/verify.py --docker`, which runs the app checks and then `docker build -t gatewatch-ci .`.

CI:

- `.github/workflows/ci.yml` runs on pushes to `main` and pull requests.
- CI uses Python 3.12 and Node 24, then runs `python scripts/verify.py --docker`.

## Important Data And Control Flows

Startup:

1. `app.py` loads an optional runtime env file from `GATEWATCH_CONFIG_FILE`, `data/gatewatch.env` on Windows, or `/etc/gatewatch/gatewatch.env` on Linux.
2. `run()` validates binding safety, initializes the SQLite store, creates the HTTP handler, and starts `GatewatchServer`.
3. Unauthenticated HTTP binds to loopback by default. Non-loopback binding requires explicit opt-in.

Persistence:

1. `Store.init()` creates and migrates the SQLite tables.
2. `employees` stores roster data, Entra sync metadata, handoff booleans, notes, and `access_profile_json`.
3. `change_requests` stores non-admin requested edits until a Domain Admin approves or rejects them.
4. `audit_log` records create, update, delete, sync, and change-request review activity.
5. `access_fields` stores configurable access-profile field definitions.
6. `access_templates` stores reusable job/access templates for prefilling employee access profiles.

HTTP/API:

1. `make_handler()` dispatches `/healthz`, `/auth/*`, `/api/*`, and static UI files.
2. `/api/bootstrap` returns summary, employees, access fields, access templates, pending change requests visible to the current user, audit entries, and auth state.
3. System administration routes use `_require_administer_system()` and employee/template modification routes use `_require_employee_modify()`, which grants admins and supervisors through the current session or trusted-proxy group headers.
4. Mutating requests reject mismatched `Origin` or `Referer` headers.
5. JSON bodies are capped by `MAX_JSON_BODY_BYTES`.

Auth:

1. Local mode has no built-in enterprise authentication and assumes loopback, tunnel, VPN, or an authenticated reverse proxy.
2. Microsoft Entra mode signs users in via `/auth/entra/login` and `/auth/entra/callback`, checks transitive group membership, and signs a local cookie.
3. Trusted-proxy mode trusts only requests carrying a configured proxy secret and maps `X-Remote-*` group headers into Gatewatch permissions.

Frontend:

1. `web/app.js` loads `/api/bootstrap` on startup.
2. Create requests post directly to `/api/employees`.
3. Existing employee edits become direct updates for admins and supervisors, and pending change requests for non-admins.
4. Tabs expose access templates, access fields, pending change reviews, directory sync, diagnostics, and runtime configuration according to the current user's permissions.

Deployment:

1. `scripts/install-ubuntu.sh` installs files under `/opt/gatewatch`, data under `/var/lib/gatewatch`, config under `/etc/gatewatch`, and creates `gatewatch.service`.
2. `Dockerfile` runs the app on `python:3.12-alpine` as a non-root `gatewatch` user, removes unused `pip` runtime files, and stores data in `/data`.
3. `scripts/deploy-container.sh` builds from the GitHub archive on a remote host, starts a read-only container with a named volume, and checks `/healthz`.
4. `docker/full-test/run_smoke.py` starts the trusted-proxy Compose lab, waits for healthy services, runs `browser_sso_smoke.py`, and tears the lab down.

## Risky Areas And Maintenance Issues

- `app.py` is large and holds many responsibilities. Small changes should be kept local and backed by targeted tests.
- Auth and authorization are sensitive. Review Entra, trusted-proxy headers, group matching, cookies, and admin-only routes carefully.
- `GATEWATCH_SESSION_SECRET`, Entra client secrets, `/etc/gatewatch/gatewatch.env`, SQLite DBs, logs, and generated `output/` files can contain sensitive company data.
- SQLite migrations and old-data compatibility are central because this app stores operational records locally.
- CSV export needs formula-injection protection; keep tests around `csv_safe_cell`.
- Shell deployment scripts include remote execution and data-volume reset paths. Treat `--reset-data`, service paths, env files, and SSH deployment as high-risk.
- `docker/full-test` is optional, so it can drift unless the core verifier checks its Python syntax.
- The project has syntax checks but no configured type checker, formatter, or Python linter.
- HTTP smoke tests start local threaded servers and can occasionally expose loopback timing flakes; use `scripts/verify.py --repeat N` when changing the test harness or server lifecycle.

## Maintenance Log

### 2026-06-30: Alpine Docker image hardening

What changed:

- Switched the production image to `python:3.12-alpine`, replaced Debian user creation with Alpine user/group creation, and removed unused `pip` runtime files from the container.
- Added Dockerfile regression checks for the Alpine base image and `pip` cleanup.
- Broadened a Windows HTTP test retry path for transient loopback connection aborts that can occur while the threaded test server is shutting down.

Validation:

- `python scripts\verify.py --docker --docker-full-test`

### 2026-06-29: Compile Docker full-test Python helpers

What changed:

- Expanded the default Python compile check to include `docker/full-test`.
- Updated verification-runner tests so the compile contract includes the trusted-proxy lab Python entrypoints.
- Added this `CODEBASE_NOTES.md` file with repo structure, commands, control flows, risk areas, and validation record.

Files changed:

- `CODEBASE_NOTES.md`
- `scripts/verify.py`
- `tests/test_app.py`
- `tests/test_verify_script.py`

Commands run and results:

- `python scripts\verify.py` before edits: passed. It ran Python compile, `unittest` discovery, and `node --check web/app.js`; Docker and full-test lab checks were skipped because they are opt-in.
- `python -m unittest tests.test_verify_script`: passed. It verified the updated verification-runner contract.
- `python scripts\verify.py --list`: passed. It showed the Python compile command now includes `docker/full-test`.
- `python -m compileall -q app.py scripts tests docker/full-test`: passed.
- `python scripts\verify.py --docker`: passed. It ran Python compile, 38 backend/UI tests with 3 expected skips, `node --check web/app.js`, and the production Docker build.
- During the merge gate, repeated Windows loopback timeouts appeared in different HTTP smoke tests. The test harness now uses a stable high-port range, proves readiness with `/healthz`, stops the test server before deleting its temp database, and retries direct socket `TimeoutError` exceptions consistently with existing `URLError` timeout retries.
- `python -m unittest tests.test_app.HttpTests.test_admin_config_requires_domain_admin_and_masks_secrets tests.test_app.HttpTests.test_trusted_proxy_auth_uses_ad_group_headers_for_admin_actions`: passed after the harness fix.
- `python -m unittest tests.test_app.HttpTests`: passed after the harness fix.
- Final `python scripts\verify.py --docker`: passed after the harness fix. It ran Python compile, 38 backend/UI tests with 3 expected skips, `node --check web/app.js`, and the production Docker build.

Remaining risks or follow-ups:

- The optional trusted-proxy lab should be run with `python3 scripts/verify.py --docker-full-test` when trusted-proxy behavior changes.
