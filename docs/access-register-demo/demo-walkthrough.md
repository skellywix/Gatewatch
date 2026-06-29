# Gatewatch Demo Walkthrough

This walkthrough shows the simulated access-governance flow in Gatewatch. The screenshots were captured from a temporary local simulation database, so they do not change the normal app database. The current UI uses a simple primary workflow, compact priority and KPI panels, visible record counts, and a richer employee profile with identity, access-summary, and local-admin customization areas.

## 1. Home

Home gives the operations view: active access, privileged access, stale reviews, pending removals, AD-disabled users, requests, risk findings, expiring access, and notifications. The smaller navigation, compact priority cards, and record-count chips keep daily work visible without hiding the inventory table.

![Home](screenshots/01-dashboard.png)

## 2. Risk Center

Risk Center is the main daily work queue. In this simulation, Active Directory flagged Avery Morgan as disabled while the user still had access. The queue routes those records to removal pending and creates a notification.

![Risk Center](screenshots/02-risk-center.png)

## 3. Offboarding

Offboarding tracks terminated employees with access that still needs evidence-backed removal. The user stays visible until the removal checklist is complete.

![Offboarding](screenshots/03-offboarding.png)

## 4. People

People shows employee and directory status side by side. AD-disabled users are flagged separately from terminated employees, so admins can review access without automatically deleting records. The refreshed form layout keeps labels and inputs readable while preserving the local role-selector MVP.

![People](screenshots/04-employees.png)

## 5. Access

Access is the source of truth for who has access to which system or location. Records include type, access level, status, owner, review state, and removal state. Selecting a row opens the employee profile with identity context, access-summary counts, directory and manual override indicators, and the admin customization form.

![Access](screenshots/05-access-inventory.png)

## 6. Access Requests

Access Requests replaces PDF access forms with tracked approvals. When a reviewer or admin approves a request, the app creates a linked access record with the requested expiration date.

![Access Requests](screenshots/06-requests.png)

## 7. Systems And Locations

Systems and locations define what employees can access. Each system has an owner, risk rating, category, and review frequency.

![Systems And Locations](screenshots/07-systems.png)

## 8. Assets

Assets covers access that is often missed by normal software exports, including shared accounts, break-glass accounts, badges, keys, fobs, and building codes.

![Assets](screenshots/08-assets.png)

## 9. Reviews

Reviews lists stale or unknown access that needs owner certification. In this simulation, review work is clear after the access records were routed into removal pending.

![Reviews](screenshots/09-reviews.png)

## 10. Governance

Governance tracks recurring review campaigns, owner accountability, backup runs, and audit export access.

![Governance](screenshots/10-governance.png)

## 11. Active Directory Sync

AD Sync accepts CSV or JSON exports and flags disabled directory accounts. The scheduler settings can replay a stored export payload for a local MVP until a direct connector is wired.

![Active Directory Sync](screenshots/11-ad-sync.png)

## 12. Imports

Imports reconciles system account exports against the employee and access inventory. Unmatched accounts become review work instead of staying hidden in spreadsheets.

![Imports](screenshots/12-imports.png)

## 13. Connectors

Connectors is the roadmap for direct integrations. It tracks which systems should move from manual CSV exports to API or directory-backed reconciliation.

![Connectors](screenshots/13-connectors.png)

## 14. Security

Security stores authentication provider and role-group mappings. Demo mode uses the local role selector, while production deployments use trusted-proxy AD or Entra identity headers.

![Security](screenshots/14-security.png)

## 15. Audit Log

The Audit Log records the evidence trail for the simulation: access request creation and approval, AD sync, disabled-user routing, review campaign creation, backup, asset creation, connector creation, and auth settings updates.

![Audit Log](screenshots/15-audit-log.png)

## Demo Flow Summary

1. Submit an access request and approve it.
2. Sync an AD export that marks an active employee as disabled.
3. Route the disabled user's access to removal pending.
4. Work the resulting item from Tasks, Risk Center, or Offboarding.
5. Create a recurring review campaign.
6. Run a backup and confirm it appears in Governance.
7. Add shared account and physical credential records.
8. Register a connector plan.
9. Save AD identity group mappings in Settings.
10. Confirm the Audit Log captured the full trail.

## Works Cited

National Institute of Standards and Technology. *Security and Privacy Controls for Information Systems and Organizations*. SP 800-53 Rev. 5, National Institute of Standards and Technology, https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final.

Microsoft. "Get-ADUser." *Microsoft Learn*, Microsoft, https://learn.microsoft.com/en-us/powershell/module/activedirectory/get-aduser.

Cybersecurity and Infrastructure Security Agency. "CISA and NSA Release Enduring Security Framework Guidance on Identity and Access Management." *CISA*, 21 Mar. 2023, https://www.cisa.gov/news-events/alerts/2023/03/21/cisa-and-nsa-release-enduring-security-framework-guidance-identity-and-access-management.
