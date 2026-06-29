# vSphere Deployment Specification

Last reviewed: 2026-06-28

This specification covers deployment of the current Gatewatch app as a Docker workload on VMware vSphere. The current app is a Python standard-library web server with a local SQLite database stored in a Docker volume. It can be deployed as a controlled internal pilot on one VM. Do not deploy active-active app nodes against the same SQLite file.

Naming note: deployment folders, environment variables, database filenames, and example AD groups still use the `AccessRegister` or `access_register` slug for compatibility with the existing app layout.

For a command-by-command technician checklist, use `docs/vsphere-technician-runbook.md`.

For the Docker and AD SSO target, use `docs/on-prem-docker-ad-sso.md`.

## Deployment Modes

| Mode | VM count | Status | Use when |
| --- | ---: | --- | --- |
| Internal pilot | 1 VM | Supported by current code | The app is used by a small internal admin group on LAN or VPN, with network access restricted. |
| Hardened pilot | 1 VM plus existing enterprise services | Recommended current target | AD or Entra groups, DNS, backup, monitoring, patching, and a TLS reverse proxy or load balancer already exist. |
| High availability | 3 or more VMs | Future architecture | Requires replacing local SQLite with a client-server database and adding real auth/session handling. |

Current recommended VM count: **1 application VM**.

Reason: Gatewatch currently writes to a local SQLite file and runs one Python process. Multiple writable app VMs would create data consistency and file locking risk unless the persistence layer is redesigned.

## VM Specification

| Item | Pilot minimum | Recommended starting spec |
| --- | --- | --- |
| VM name | `AR-APP01` | `AR-APP01` |
| Guest OS | Docker-capable Linux or Windows Server | Linux VM with Docker Engine, or Windows Server with Docker Desktop or approved container runtime |
| vCPU | 2 | 4 if imports are large or more than 25 operators use the app |
| Memory | 4 GB | 8 GB |
| OS disk | 80 GB thin-provisioned VMDK | 100 GB thin-provisioned VMDK |
| Data disk | 40 GB VMDK | 100 GB VMDK mounted as `D:` |
| Network | 1 VMXNET3 adapter | 1 VMXNET3 adapter on an internal server VLAN |
| Firmware | vSphere template default | Secure Boot if supported by the OS template |
| Tools | VMware Tools installed | VMware Tools installed and maintained |
| Time | Domain time sync | Domain time sync, with vSphere time behavior aligned to site standard |

Suggested data layout:

```text
D:\AccessRegister\
  app\                 # checked-out app files
  data\                # access_register.db
  data\backups\        # in-app DB backups
  logs\                # service stdout/stderr logs
  import-drop\         # optional protected AD export drop
```

## Operating System Baseline

Use a current, patched Windows Server template that already includes:

- Domain join.
- EDR or antivirus.
- Windows firewall enabled.
- VMware Tools.
- Central log collection if available.
- Standard backup agent or vSphere backup protection.
- Least-privilege local administrators.

Docker Engine and the Docker Compose plugin are required on the VM. Python runs inside the container image and does not need to be installed on the host unless the AD sync job runs there.

## Network Specification

| Flow | Port | Source | Destination | Notes |
| --- | --- | --- | --- | --- |
| User access | TCP 443 | Internal users on LAN or VPN | Reverse proxy or load balancer | Terminate TLS and authenticate users before traffic reaches the app. |
| App backend | TCP 8087 | VM loopback, same-host reverse proxy, or approved proxy host only | `AR-APP01` | Compose binds to `127.0.0.1:8087` by default. Do not expose to user subnets. |
| App health check | TCP 8087 | VM-local Docker healthcheck or approved monitoring host | `AR-APP01` | Check `/healthz`; it returns only service and database health. |
| Admin access | RDP 3389 or site remote admin tool | Admin workstation subnet | `AR-APP01` | Restrict to infrastructure admins. |
| Backup | Site-specific | Backup service | `AR-APP01` and backup repository | Back up the app folder, database, logs, and exported backups. |
| AD export input | SMB or protected copy path | AD export job host | `AR-APP01\D$\AccessRegister\import-drop` or approved share | Only if scheduled AD export is automated outside the app. |

The Docker profile binds the app backend to `127.0.0.1:8087` by default:

```powershell
GATEWATCH_BIND_ADDRESS=127.0.0.1
GATEWATCH_APP_PORT=8087
```

Change `GATEWATCH_BIND_ADDRESS` only when the reverse proxy runs on another host and host firewall rules restrict TCP 8087 to that proxy.

## User and Service Accounts

| Account or group | Type | Purpose | Required access |
| --- | --- | --- | --- |
| `DOMAIN\gmsa-ar-app$` | Group managed service account preferred | Runs the Gatewatch process | Read and execute app files. Modify `D:\AccessRegister\data` and `D:\AccessRegister\logs`. No local admin after install. |
| `DOMAIN\svc-ar-app` | Domain service account fallback | Runs the app if gMSA is not available | Same as gMSA. Password vaulted and rotated by policy. |
| `DOMAIN\gmsa-ar-adsync$` | Group managed service account preferred | Runs external AD export job, if used | Read only the AD attributes needed for sync. Write only to approved export drop. |
| `DOMAIN\AccessRegister-Admins` | AD or Entra group | Target Admin role | App administration, imports, AD sync, backups, auth settings. |
| `DOMAIN\AccessRegister-Supervisors` | AD or Entra group | Target Supervisor role | Resource creation, business approval, access certification, and removals. |
| `DOMAIN\AccessRegister-Reviewers` | AD or Entra group | Target Reviewer role | Reviews, request decisions, campaign completion. |
| `DOMAIN\AccessRegister-HR` | AD or Entra group | Target HR role | Employee and offboarding workflows. |
| `DOMAIN\AccessRegister-ReadOnly` | AD or Entra group | Target ReadOnly role | Inventory and evidence visibility without writes. |
| `DOMAIN\vSphere-AccessRegister-Ops` | vCenter group | Operates the VM | Least-privilege vSphere role scoped to the VM folder or resource pool. |
| `DOMAIN\vSphere-AccessRegister-Backup` | vCenter or backup role | Backup platform access | Backup and restore permissions scoped to the app VM. |
| Local break-glass admin | Local Windows account | Emergency access | Disabled or vaulted by policy, monitored, and excluded from daily use. |

Important: production access should use `ACCESS_REGISTER_AUTH_MODE=trusted_proxy` behind an authenticated reverse proxy. The app maps trusted AD or Entra group headers to Gatewatch roles and ignores browser-supplied role headers in that mode.

## File System Permissions

Apply NTFS permissions so the service account can run the app without broad server control:

| Path | Service account | App admins | Local administrators |
| --- | --- | --- | --- |
| `D:\AccessRegister\app` | Read and execute | Modify | Full control |
| `D:\AccessRegister\data` | Modify | Modify | Full control |
| `D:\AccessRegister\logs` | Modify | Modify | Full control |
| `D:\AccessRegister\import-drop` | Read, optional delete after import | Modify | Full control |

Do not grant `Everyone` or broad domain user groups access to the database, backups, logs, or saved AD export payloads.

## Docker Install Procedure

1. Create `AR-APP01` from the approved VM template.
2. Assign CPU, memory, OS disk, and data disk using the VM spec above.
3. Patch the OS, install VMware Tools, join the domain if site policy requires it, and apply the server baseline.
4. Install Docker Engine and the Docker Compose plugin from an approved internal package source.
5. Copy the app files into the approved application folder.
6. Create the vSphere Docker environment file:

```powershell
Copy-Item docker/vsphere/.env.example docker/vsphere/.env
notepad docker/vsphere/.env
```

Set `ACCESS_REGISTER_PROXY_SECRET`, AD role groups, and keep `GATEWATCH_BIND_ADDRESS=127.0.0.1` unless the reverse proxy runs on a different host and the VM firewall allows only that proxy.

7. Start the container:

```powershell
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml up -d --build
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml ps
```

8. Create a Windows Firewall or host firewall inbound rule that allows user traffic only to the reverse proxy on TCP 443. Keep the app port on loopback or allow TCP 8087 only from the reverse proxy host.
9. Confirm local container health from the VM:

```powershell
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml ps
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml logs --tail 100 app
```

10. Configure the AD-authenticated TLS reverse proxy for `https://gatewatch.company.local` and complete the smoke workflow in `AGENTS.md`.

## Production AD Sync Job

Run directory sync as a scheduled task under `DOMAIN\gmsa-ar-adsync$` or `DOMAIN\svc-ar-adsync`. The account needs read-only access to the imported AD user attributes and membership in the Gatewatch Admin mapping group when the job authenticates through the reverse proxy.

Example scheduled-task command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "D:\AccessRegister\app\scripts\sync-active-directory.ps1" `
  -GatewatchUrl "https://gatewatch.company.local" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -UseDefaultCredentialsForSso
```

For a direct server-side job on the app host, keep TCP 8087 blocked from user subnets, set `ACCESS_REGISTER_PROXY_SECRET`, and pass `-RemoteUser` plus `-RemoteGroups` so the app receives a trusted service-account identity.

## Backup and Recovery

The app has an in-app backup action that copies the SQLite database into `D:\AccessRegister\data\backups` and records the run in `backup_runs`. Use it for operator-triggered evidence backups, but do not rely on it as the only recovery control.

Backup retention is accepted from 1 to 3650 days. Backup filenames include sub-second precision so repeated backups do not overwrite each other. Successful in-app backup runs prune expired managed backup files and mark the expired run with `pruned_at`. Backup paths are operationally sensitive and are only returned in Admin API payloads.

Production backup should include:

- The SQLite database.
- `D:\AccessRegister\data\backups`.
- Server logs.
- The exact deployed app version.
- Scheduled AD export files only if retention policy allows them.

Recommended starting targets:

| Control | Target |
| --- | --- |
| RPO | 24 hours for pilot, shorter if this becomes the source of truth for access reviews. |
| RTO | 4 hours for pilot restore to a replacement VM. |
| Backup frequency | Nightly VM or file-level backup plus on-demand in-app backup before major imports. |
| Retention | 30 days operational, longer only if required by audit or legal policy. |
| Restore test | Quarterly or before production signoff. |

Do not treat vSphere snapshots as the backup strategy. Use snapshots only as short-lived deployment checkpoints, then remove them after validation.

## Validation

Run these checks on the deployed VM before handoff:

```powershell
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml config
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml ps
docker compose --env-file docker/vsphere/.env -f docker/vsphere/compose.yaml logs --tail 100 app
```

Use the health-check command in `docker/vsphere/README.md` to confirm `/healthz` reports service and database health.

Before deploying a new app revision, run the repository test gate on the build workstation:

```powershell
python scripts\verify.py --docker
docker compose --env-file docker/vsphere/.env.example -f docker/vsphere/compose.yaml config
```

Then complete a manual UI check:

1. Open the dashboard.
2. Inspect an employee from the inventory table.
3. Certify a stale record from Reviews.
4. Mark a removal item complete with evidence.
5. Import sample CSV and confirm unmatched accounts increase.
6. Sync sample AD CSV and confirm disabled-directory flags appear.
7. Create and approve an access request.
8. Run a backup and confirm a backup run appears.
9. Confirm Audit Log shows the actions.

## Production Gaps to Close

Before Gatewatch becomes an authoritative production access-control system, close these gaps:

- Keep all user access behind the authenticated TLS reverse proxy.
- Keep Supervisor rollout tied to accurate HR or AD manager data; trusted-proxy Supervisor users are scoped to their own employee row and direct reports.
- Decide whether SQLite remains acceptable or migrate to a managed database.
- Store connector secrets outside SQLite.
- Forward audit logs to protected central logging.
- Define retention for audit logs, imports, AD exports, removal evidence, and off-host infrastructure backups.

## References

Broadcom. "vSphere Virtual Machine Administration." *VMware vSphere Documentation*, https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-virtual-machine-administration.html. Accessed 28 June 2026.

Broadcom. "vSphere Security." *VMware vSphere Documentation*, https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-security.html. Accessed 28 June 2026.

Microsoft. "Hardware Requirements for Windows Server." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/get-started/hardware-requirements. Accessed 28 June 2026.

Microsoft. "Group Managed Service Accounts Overview." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/security/group-managed-service-accounts/group-managed-service-accounts-overview. Accessed 28 June 2026.

Python Software Foundation. "venv - Creation of Virtual Environments." *Python 3 Documentation*, https://docs.python.org/3/library/venv.html. Accessed 28 June 2026.

SQLite Consortium. "Online Backup API." *SQLite Documentation*, https://www.sqlite.org/backup.html. Accessed 28 June 2026.
