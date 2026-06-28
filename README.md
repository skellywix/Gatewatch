# Eric Gatewatch

Eric Gatewatch is a local internal web app for tracking who has access to company systems, physical locations, building codes, badges, shared resources, and privileged accounts.

It replaces hand-filled PDF access forms as the source of truth with a searchable inventory, CSV account reconciliation, request workflow, review campaigns, offboarding queues, risk findings, backups, and an audit trail.

## What It Does

- Tracks employees and employment status.
- Tracks systems, applications, locations, product names, standard URLs, and accountable owners.
- Tracks access records with level, type, status, business reason, approval, owner, review date, expiration date, MFA evidence, rotation due date, removal due date, and removal evidence.
- Captures access requests, approval or denial decisions, and approved temporary access expiration dates.
- Imports CSV account exports and flags unmatched or terminated-employee accounts.
- Syncs Active Directory CSV or JSON exports to create new users, update directory metadata, and flag disabled AD users.
- Can run a scheduled AD sync from a saved export payload when enabled.
- Lets admins protect local employee customizations from AD overwrites.
- Routes active access to removal when an employee is marked terminated or when AD flags the user disabled.
- Surfaces a disabled-user access queue, risk findings, expiring access, overdue reviews, and pending notifications.
- Tracks recurring review campaigns and an owner accountability dashboard.
- Tracks shared accounts, break-glass credentials, and physical credentials such as badges, building codes, and keys.
- Tracks connector plans for systems that should move from CSV reconciliation to direct integration.
- Stores production authentication mapping settings for AD or Entra role groups.
- Creates local SQLite backups and exports the audit log as CSV.
- Requires evidence before access can be marked removed.
- Records create, update, review, import, sync, backup, and removal actions in the audit log.

## Run Locally

No package install is required. The app uses Python standard library modules and SQLite.

```powershell
cd C:\Users\Eric\Desktop\Coding_Projects\access-inventory-app
python app.py
```

Open:

```text
http://127.0.0.1:8087
```

The default database is created at:

```text
data/access_register.db
```

To use another database path:

```powershell
$env:ACCESS_REGISTER_DB="C:\AccessRegister\access_register.db"
python app.py
```

To disable the background scheduled AD sync worker during local testing:

```powershell
$env:ACCESS_REGISTER_SCHEDULER="0"
python app.py
```

## Test

```powershell
python -m unittest discover -s tests
python -m py_compile app.py
node --check web\app.js
```

## Documentation

- [Access control model](docs/access-control.md): current role behavior, route-level authorization, audit behavior, and production identity gaps.
- [vSphere deployment specification](docs/vsphere-deployment.md): VM count, OS, sizing, network rules, service accounts, deployment steps, backup expectations, and production gaps.
- [vSphere technician runbook](docs/vsphere-technician-runbook.md): command-by-command PowerCLI and PowerShell deployment path for the current single-VM pilot.

## CSV Import Format

The importer accepts common account-export columns. These headers are supported:

- Employee match: `employee_id`, `employee`, `id`, `employee_number`
- Email match: `email`, `user_email`, `mail`
- Name match: `name`, `full_name`, `display_name`, `display`
- Account: `account`, `username`, `user`, `login`
- Access level: `access_level`, `role`, `permission`, `group`
- Access type: `access_type`, `type`

Example:

```csv
employee_id,email,name,account,role,access_type
E-1001,avery.morgan@example.local,Avery Morgan,avery.admin,Administrator,admin
,unknown.contractor@example.local,Unknown Contractor,contractor.ext,Administrator,admin
```

## Active Directory Sync

The AD Sync view accepts CSV or JSON exported from Active Directory. It matches users by AD object GUID, employee ID, email, UPN, or SAM account name. New users are created automatically. Existing users are updated with directory metadata. Disabled AD users are flagged with `AD disabled` without automatically deleting records or marking the employee terminated.

Recommended PowerShell JSON export:

```powershell
Get-ADUser -Filter * -Properties EmployeeID,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName,DistinguishedName,LastLogonDate |
  Select EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName,DistinguishedName,LastLogonDate |
  ConvertTo-Json
```

Supported AD fields include:

- `EmployeeID`, `EmployeeNumber`, or `SamAccountName`
- `Name`, `DisplayName`, `GivenName`, `Surname`
- `Mail`, `Email`, `UserPrincipalName`
- `Department`, `Office`, `PhysicalDeliveryOfficeName`, `Manager`
- `Enabled`, `Disabled`
- `ObjectGUID`, `DistinguishedName`, `LastLogonDate`

Admins can edit an employee from the selected employee detail panel and check `Protect these manual details from AD sync`. Future AD syncs still update AD metadata such as enabled/disabled state, object GUID, SAM account, UPN, DN, and last sync time, but preserve the local name, email, department, location, and manager fields.

The AD Sync view also has scheduled sync settings. The current scheduler replays the saved CSV or JSON export at the configured interval, which is useful for a local MVP or a scripted export job. Treat the saved directory export as sensitive data and keep the database path protected. A production connector should replace this with a service account or delegated Microsoft Graph or LDAP integration.

## Governance Workflow

- Use Requests to capture access requests and approve or deny them. Approved requests create an access record and keep the request linked to that record.
- Use Reviews to certify active access records and capture review notes.
- Use Governance to create review campaigns by owner and due date.
- Use Risk Center to work disabled-user access, expired access, overdue removals, shared-account issues, and notifications.
- Use Offboarding to close removal items. Removed access must include evidence.
- Use Assets to track shared accounts and physical access that may not appear in a normal system export.
- Use Connectors to keep a backlog of systems that need direct reconciliation instead of manual CSV imports.
- Use Security to store the intended AD or Entra authentication provider and role-group mappings.
- Use Governance to run backups and download `audit-log.csv` for evidence requests.

## Current MVP Boundary

This version is designed for an internal LAN or VPN deployment. The in-app role selector demonstrates role-gated behavior, but it is not a replacement for production authentication. The Security view stores target identity-provider settings, but the HTTP server still needs real login middleware, TLS, server-side user identity, a managed retention policy, and connector-specific credential handling before it becomes an authoritative production system.
