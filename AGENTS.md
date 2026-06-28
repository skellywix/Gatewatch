# Gatewatch Instructions

## Goal

Gatewatch is an internal access inventory web app. It tracks employees, systems and locations, access records, access requests, CSV account imports, AD sync status, review campaigns, risk findings, shared accounts, physical credentials, offboarding removals, backups, and audit evidence.

## Local Run

Use Python 3.12 or newer. The app has no third-party dependencies.

```powershell
python app.py
```

Open `http://127.0.0.1:8087`.

Optional environment variables:

```powershell
$env:ACCESS_REGISTER_HOST="127.0.0.1"
$env:ACCESS_REGISTER_PORT="8087"
$env:ACCESS_REGISTER_DB="C:\path\to\access_register.db"
$env:ACCESS_REGISTER_SCHEDULER="0"
python app.py
```

## Verification

Run the backend lifecycle tests:

```powershell
python -m unittest discover -s tests
```

Run the automated UI workflow smoke test:

```powershell
python -m unittest tests.test_ui_smoke
```

Run a manual UI check:

1. Open the dashboard.
2. Inspect an employee from the access inventory table.
3. Certify a stale record from Reviews.
4. Mark a terminated employee's removal item complete with evidence using the in-app removal evidence dialog.
5. Import the sample CSV from Imports and confirm unmatched accounts increase.
6. Sync the sample AD CSV from AD Sync and confirm new users plus disabled-directory flags appear.
7. Edit an employee from the detail panel, enable manual override, sync AD again, and confirm local fields are preserved while AD enabled/disabled state updates.
8. Create an access request, approve it, and confirm the created access record keeps the expiration date.
9. Route disabled-user access from Risk Center and confirm it moves to removal pending.
10. Create a review campaign from Governance and mark it complete.
11. Add a shared account and a physical credential from Assets.
12. Add a connector plan and update Security authentication settings.
13. Run a backup from Governance and confirm the backup run appears.
14. Check Audit Log for the recorded actions.

## Guardrails

- Do not remove audit-log writes from create, update, review, import, or removal paths.
- Do not mark access `removed` without evidence.
- Keep historical records. Prefer status changes over deletion.
- AD sync must never delete employees or access records. It should create/update identity records and flag disabled AD users for review.
- Scheduled AD sync currently replays a saved export payload. Treat that payload and the SQLite database as sensitive company data.
- Admin override protects local name, email, department, location, and manager values from AD overwrites while still allowing AD metadata to refresh.
- Access request approval should create access records only after reviewer or admin action.
- Removed access must retain removal evidence, and shared or physical credentials should be closed by status changes instead of deletion.
- The role selector is an MVP authorization control for this local app, not enterprise authentication. Production deployment needs real identity provider integration, TLS, server-side user identity, retention policy, and secure connector credential handling.
