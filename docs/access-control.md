# Access Control Model

Last reviewed: 2026-06-28

This document describes how Access Register access control works in the current codebase and what must be added before the app is trusted as a production authorization system.

## Current Boundary

Access Register currently uses a local role selector in the browser. The selected role is stored in browser `localStorage` and sent to the server on API calls as `X-App-Role`; the displayed actor is sent as `X-App-Actor`.

The server enforces mutating API actions with a role permission table and route-level allowlists. This prevents accidental in-app writes by lower-privilege roles, but it is not enterprise authentication. Any HTTP client that can reach the app can choose its own role header today. Treat the current control as an MVP authorization model for an internal pilot only.

Production use requires server-side login, trusted identity provider claims, TLS, a server-derived actor identity, session or token validation, and tamper-resistant audit forwarding.

## Protected Data

The app stores sensitive company access data in SQLite:

- Employees, employment status, locations, departments, managers, and AD metadata.
- Systems, physical locations, shared resources, and accountable owners.
- Access records, privileged access, shared accounts, physical credentials, business reasons, approvals, reviews, expiration dates, MFA notes, rotation dates, and removal evidence.
- CSV imports and saved scheduled AD sync payloads.
- Authentication mapping settings for the intended AD or Entra groups.
- Audit log entries and local database backups.

The database file, backups, scheduler payload, exported audit CSV, and server logs must be treated as sensitive operational evidence.

## Role Model

The current server role permissions are defined in `ROLE_PERMISSIONS`:

| Role | Permission verbs | Intended use |
| --- | --- | --- |
| Admin | `create`, `update`, `review`, `import` | Full local administration, system setup, imports, AD sync, backups, auth settings, and governance setup. |
| Reviewer | `review` | Certify access, route access to removal, decide access requests, and complete review campaigns. |
| HR | `create`, `update` | Create and update employee-facing records, submit access requests, track physical credentials, route disabled-user access, and add removal evidence. |
| ReadOnly | none | Read inventory, governance, risk, backup, auth setting, and audit views without making changes. |

The route allowlist still narrows each verb. For example, HR has the `create` verb, but HR cannot create systems, direct access records, shared accounts, connectors, imports, AD syncs, backups, or auth settings because those routes are still Admin-only.

## Server Enforcement

The server accepts read routes without a role check. This means any client that can reach the app can read inventory, audit, governance, and settings data in the current MVP. Network isolation is required until real authentication is implemented.

Write routes call `_require(role, permission, allowed_roles)`. A request must pass both checks:

- The role must be in the endpoint-specific `allowed_roles`.
- The role must have the requested permission verb in `ROLE_PERMISSIONS`.

Current write access by workflow:

| Workflow | Endpoint examples | Allowed roles |
| --- | --- | --- |
| Employees | `POST /api/employees`, `PATCH /api/employees/{id}` | Admin, HR |
| Manual override fields | `admin_override`, `admin_notes` on employee PATCH | Admin only |
| Systems and locations | `POST /api/systems` | Admin |
| Direct access records | `POST /api/access-records` | Admin |
| Access review decisions | `POST/PATCH /api/access-records/{id}/review` | Admin, Reviewer |
| Removal evidence and access updates | `PATCH /api/access-records/{id}` | Admin, HR |
| Account CSV import | `POST /api/imports/accounts` | Admin |
| AD sync and scheduled AD sync | `POST /api/ad/sync`, `POST /api/ad-sync-settings`, `POST /api/ad/run-scheduled` | Admin |
| Access requests | `POST /api/access-requests` | Admin, HR |
| Request decisions | `POST /api/access-requests/{id}/decision` | Admin, Reviewer |
| Review campaigns | `POST/PATCH /api/review-campaigns` | Admin, Reviewer |
| Notifications | `PATCH /api/notifications/{id}` | Admin, Reviewer, HR |
| Shared accounts | `POST /api/shared-accounts` | Admin |
| Physical credentials | `POST /api/physical-credentials` | Admin, HR |
| Connector plans | `POST /api/connectors` | Admin |
| Backups | `POST /api/backups/run` | Admin |
| Auth settings | `POST /api/auth-settings` | Admin |
| Disabled-user removal routing | `POST /api/disabled-access/route-removal` | Admin, HR |

## Workflow Controls

The app has several business controls that are enforced below the UI:

- Access cannot be marked `removed` unless `removal_evidence` is supplied.
- Review decisions can only be `certified` or `remove`.
- A reviewer route-to-remove action sets access to `removal_pending` and gives a default three-day removal due date.
- Access request approval creates an access record only after an Admin or Reviewer decision.
- Access request denial does not create an access record.
- AD sync creates or updates employee identity records and flags disabled AD users. It does not delete employees or access records.
- Admin override protects local name, email, department, location, and manager values from AD overwrites while AD metadata continues to refresh.
- CSV imports create records for matched active employees and flag unmatched or inactive-employee accounts for review.
- Backups are logged in `backup_runs`, and important write paths create audit log entries.

## Audit Behavior

The app writes audit log entries for seed data, creates, updates, review decisions, request decisions, imports, AD syncs, disabled-user routing, backups, auth setting updates, notifications, and removal-related updates.

Each audit entry includes:

- Actor string from `X-App-Actor` or the scheduler.
- Role string from `X-App-Role`.
- Action, entity type, entity ID, summary, and timestamp.
- Before and after JSON for many write paths.

Current limitation: actor and role values are client-supplied except for the scheduler. Production auth must derive actor and role on the server from trusted identity claims. For higher assurance, forward audit events to a protected log store or SIEM so local database changes cannot rewrite the only evidence trail.

## Production Identity Target

The Security view stores intended authentication mapping settings:

- Provider: local role selector, Active Directory, or Microsoft Entra ID.
- Login required flag.
- Admin, Reviewer, HR, and ReadOnly group names.
- Notes for implementation detail.

Those settings are planning data today. They do not yet enforce login. The production design should map trusted AD or Entra group claims to the four application roles:

| App role | Suggested group |
| --- | --- |
| Admin | `DOMAIN\AccessRegister-Admins` |
| Reviewer | `DOMAIN\AccessRegister-Reviewers` |
| HR | `DOMAIN\AccessRegister-HR` |
| ReadOnly | `DOMAIN\AccessRegister-ReadOnly` |

Recommended production requirements:

- Require authentication for every route, including static UI and all reads.
- Reject client-supplied role and actor headers from untrusted clients.
- Derive role from AD or Entra group membership.
- Derive actor from the authenticated user principal.
- Require TLS from the user to the reverse proxy or app endpoint.
- Add CSRF protection if browser sessions use cookies.
- Set session timeout and reauthentication policy through the identity provider.
- Store connector credentials outside SQLite, preferably in an approved secrets manager.
- Apply retention policy to audit logs, backups, AD exports, and import payloads.

## References

Joint Task Force. "Security and Privacy Controls for Information Systems and Organizations." *NIST Special Publication 800-53 Revision 5*, National Institute of Standards and Technology, 2020, https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final. Accessed 28 June 2026.

Microsoft. "Group Managed Service Accounts Overview." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/security/group-managed-service-accounts/group-managed-service-accounts-overview. Accessed 28 June 2026.

Python Software Foundation. "http.server - HTTP Servers." *Python 3 Documentation*, https://docs.python.org/3/library/http.server.html. Accessed 28 June 2026.
