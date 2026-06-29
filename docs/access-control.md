# Access Control Model

Last reviewed: 2026-06-28

This document describes how Eric Gatewatch access control works in the current codebase and what must still be controlled before the app is trusted as a production authorization system.

Naming note: `AccessRegister` and `access_register` remain compatibility slugs for environment variables, database filenames, example Windows paths, and example AD groups. The current user-facing product name is Eric Gatewatch.

## Current Boundary

Eric Gatewatch now has two authentication modes:

- `local`: development and demo mode. The browser role selector is active, and the selected role is sent as `X-App-Role`. The app refuses local mode on a non-loopback bind unless `ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=1` is set for an isolated demo.
- `trusted_proxy`: on-prem production target. A reverse proxy performs AD SSO and injects trusted identity headers. The app ignores browser-supplied role and actor headers, derives the actor from proxy identity, maps AD groups to app roles, and scopes Employee users to their linked employee record.

Production use requires TLS at the reverse proxy, a direct-container network block, header stripping at the proxy, trusted identity provider claims, and tamper-resistant audit forwarding.

## Protected Data

The app stores sensitive company access data in SQLite:

- Employees, employment status, locations, departments, managers, and AD metadata.
- Systems, physical locations, shared resources, configurable business categories, and accountable owners.
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
| Supervisor | `create`, `update`, `review` | Business owner workflow: create business categories and resources, submit and approve requests, certify access, add removal evidence, and route disabled-user access. |
| Reviewer | `review` | Certify access, route access to removal, decide access requests, and complete review campaigns. |
| HR | `create`, `update` | Create and update employee-facing records, submit access requests, track physical credentials, route disabled-user access, and add removal evidence. |
| Employee | `create` | Trusted-proxy self-service: view own access and submit access requests only for the linked employee record. |
| ReadOnly | none | Read inventory, governance, risk, redacted backup history, auth setting, and audit views without making changes. |

The route allowlist still narrows each verb. For example, Supervisor has the `create` verb, but Supervisor cannot run imports, AD syncs, backups, auth settings, connector setup, or shared-account setup because those routes are still Admin-only.

## Server Enforcement

In `local` mode, most read routes remain open to support the development role selector. Treat this as a demo mode only. Startup blocks local mode on `0.0.0.0` or other non-loopback addresses by default.

In `trusted_proxy` mode, every static and API request must include a proxy-authenticated user header. Employee users receive a scoped bootstrap payload and can read only their own employee record, access records, and access requests. Operational read routes such as audit, imports, backups, offboarding, risk findings, AD sync runs, shared accounts, connectors, and auth settings are denied to Employee users.

Scheduled AD payloads and backup filesystem paths are narrower: the saved AD payload is only returned to Admin bootstrap or `GET /api/ad-sync-settings`, and backup paths are hidden from non-admin backup payloads.

Write routes call `_require(role, permission, allowed_roles)`. A request must pass both checks:

- The role must be in the endpoint-specific `allowed_roles`.
- The role must have the requested permission verb in `ROLE_PERMISSIONS`.

Current write access by workflow:

| Workflow | Endpoint examples | Allowed roles |
| --- | --- | --- |
| Employees | `POST /api/employees`, `PATCH /api/employees/{id}` | Admin, HR |
| Manual override fields | `admin_override`, `admin_notes` on employee PATCH | Admin only |
| Business categories | `POST /api/resource-categories` | Admin, Supervisor |
| Systems, locations, and resources | `POST /api/systems` | Admin, Supervisor |
| Direct access records | `POST /api/access-records` | Admin, Supervisor |
| Access review decisions | `POST/PATCH /api/access-records/{id}/review` | Admin, Supervisor, Reviewer |
| Removal evidence and access updates | `PATCH /api/access-records/{id}` | Admin, Supervisor, HR |
| Account CSV import | `POST /api/imports/accounts` | Admin |
| AD sync and scheduled AD sync | `POST /api/ad/sync`, `POST /api/ad-sync-settings`, `POST /api/ad/run-scheduled` | Admin |
| Access requests | `POST /api/access-requests` | Admin, Supervisor, HR, Employee for self only |
| Request decisions | `POST /api/access-requests/{id}/decision` | Admin, Supervisor, Reviewer |
| Review campaigns | `POST/PATCH /api/review-campaigns` | Admin, Supervisor, Reviewer |
| Notifications | `PATCH /api/notifications/{id}` | Admin, Supervisor, Reviewer, HR |
| Shared accounts | `POST /api/shared-accounts` | Admin |
| Physical credentials | `POST /api/physical-credentials` | Admin, HR |
| Connector plans | `POST /api/connectors` | Admin |
| Backups | `POST /api/backups/run` | Admin |
| Auth settings | `POST /api/auth-settings` | Admin |
| Disabled-user removal routing | `POST /api/disabled-access/route-removal` | Admin, Supervisor, HR |

## Workflow Controls

The app has several business controls that are enforced below the UI:

- Access cannot be marked `removed` unless `removal_evidence` is supplied.
- Access requests must use a supported access type before they can be approved into an access record.
- Employee role access requests must target the authenticated user's linked employee record.
- Review decisions can only be `certified` or `remove`.
- A reviewer route-to-remove action sets access to `removal_pending` and gives a default three-day removal due date.
- Access request approval creates an access record only after an Admin or Reviewer decision.
- Access request denial does not create an access record.
- AD sync creates or updates employee identity records and flags disabled AD users. It does not delete employees or access records.
- Admin override protects local name, email, department, location, and manager values from AD overwrites while AD metadata continues to refresh.
- CSV imports create records for matched active employees and flag unmatched or inactive-employee accounts for review.
- Backups are logged in `backup_runs`, use collision-resistant filenames, require retention between 1 and 3650 days, hide filesystem paths from non-admin read payloads, and mark expired managed backup files with `pruned_at` after successful backup runs.
- JSON API request bodies are limited to 5 MiB, and invalid `Content-Length` headers return controlled client errors.
- Trusted-proxy mutating requests must include `X-Requested-With: XMLHttpRequest` and are rejected when browser fetch metadata says the request is cross-site.

## Audit Behavior

The app writes audit log entries for seed data, creates, updates, review decisions, request decisions, imports, AD syncs, disabled-user routing, backups, auth setting updates, notifications, and removal-related updates.

Each audit entry includes:

- Actor string from `X-App-Actor`, trusted proxy identity, service job, or the scheduler.
- Role string from `X-App-Role` in local mode or trusted proxy group mapping in production mode.
- Action, entity type, entity ID, summary, and timestamp.
- Before and after JSON for many write paths.

Current limitation: actor and role values are client-supplied in `local` mode. In `trusted_proxy` mode, actor and role are server-derived from proxy identity and AD group mappings. For higher assurance, forward audit events to a protected log store or SIEM so local database changes cannot rewrite the only evidence trail.

## Production Identity Target

The Security view stores intended authentication mapping settings:

- Provider: local role selector, Active Directory, or Microsoft Entra ID.
- Login required flag.
- Admin, Supervisor, Reviewer, HR, and ReadOnly group names.
- Notes for implementation detail.

Those settings are used by `trusted_proxy` mode to map trusted AD or Entra group claims to application roles:

| App role | Suggested group |
| --- | --- |
| Admin | `DOMAIN\AccessRegister-Admins` |
| Supervisor | `DOMAIN\AccessRegister-Supervisors` |
| Reviewer | `DOMAIN\AccessRegister-Reviewers` |
| HR | `DOMAIN\AccessRegister-HR` |
| ReadOnly | `DOMAIN\AccessRegister-ReadOnly` |
| Employee | Any authenticated, linked employee not in a higher mapped group |

Recommended production requirements:

- Put the app in `ACCESS_REGISTER_AUTH_MODE=trusted_proxy`.
- Require authentication for every route at the reverse proxy and app.
- Strip client-supplied identity headers before injecting proxy-owned headers.
- Derive role from AD or Entra group membership.
- Derive actor from the authenticated user principal.
- Require TLS from the user to the reverse proxy or app endpoint.
- Keep the app container reachable only from the reverse proxy network, or set `ACCESS_REGISTER_PROXY_SECRET`.
- Set session timeout and reauthentication policy through the identity provider.
- Store connector credentials outside SQLite, preferably in an approved secrets manager.
- Apply retention policy to audit logs, backups, AD exports, and import payloads.
- Add department, manager-chain, or explicit team scoping before giving Supervisor role broad production use.
- Require a green GitHub CI run for Python compile, backend and UI smoke tests, frontend syntax, and container build before promoting a new image.

## References

Joint Task Force. "Security and Privacy Controls for Information Systems and Organizations." *NIST Special Publication 800-53 Revision 5*, National Institute of Standards and Technology, 2020, https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final. Accessed 28 June 2026.

Microsoft. "Group Managed Service Accounts Overview." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/security/group-managed-service-accounts/group-managed-service-accounts-overview. Accessed 28 June 2026.

Python Software Foundation. "http.server - HTTP Servers." *Python 3 Documentation*, https://docs.python.org/3/library/http.server.html. Accessed 28 June 2026.
