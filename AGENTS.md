# Gatewatch Instructions

## Goal

Gatewatch is now a simple Ubuntu-friendly employee tracker. Keep it focused:

- Create an employee.
- Track the employee in SQLite.
- Edit the employee.
- Delete the employee.
- Track the access request handoff with step-style controls:
  request received, manager approved, IT provisioned, employee notified.

Avoid rebuilding the older broad inventory platform unless Eric explicitly asks for it again.

## Local Run

Use Python 3.10 or newer. The app has no third-party Python dependencies.

```bash
python3 app.py
```

Open `http://127.0.0.1:8087`.

Optional environment variables:

```bash
export GATEWATCH_HOST=127.0.0.1
export GATEWATCH_PORT=8087
export GATEWATCH_DB=/path/to/gatewatch.db
export GATEWATCH_CONFIG_FILE=/path/to/gatewatch.env
python3 app.py
```

## Ubuntu Install

```bash
curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash
```

The installer can run from a local checkout or download the GitHub source archive itself. It copies the app to `/opt/gatewatch`, stores SQLite data in `/var/lib/gatewatch`, and installs `gatewatch.service`.

## Verification

```bash
python3 scripts/verify.py
python3 scripts/verify.py --docker
python3 scripts/verify.py --docker-full-test
```

The important functional proof is that employee create, edit, step workflow, delete, database persistence, and audit export all pass.
Use `--docker-full-test` when trusted-proxy, reverse-proxy, or browser SSO behavior changes.

## Guardrails

- Keep the app simple and spreadsheet-like, but nicer.
- Keep the first screen usable. Do not add a landing page.
- Do not add new production dependencies unless the user asks.
- Keep unauthenticated HTTP bound to loopback by default.
- Treat the SQLite database as sensitive company data.
- Do not remove the employee CRUD tests or the Ubuntu installer tests when changing this workflow.
