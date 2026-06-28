# vSphere Deployment Specification

Last reviewed: 2026-06-28

This specification covers deployment of the current Access Register app on VMware vSphere. The current app is a Python standard-library web server with a local SQLite database. It can be deployed as a controlled internal pilot on one VM. Do not deploy active-active app nodes against the same SQLite file.

For a command-by-command technician checklist, use `docs/vsphere-technician-runbook.md`.

## Deployment Modes

| Mode | VM count | Status | Use when |
| --- | ---: | --- | --- |
| Internal pilot | 1 VM | Supported by current code | The app is used by a small internal admin group on LAN or VPN, with network access restricted. |
| Hardened pilot | 1 VM plus existing enterprise services | Recommended current target | AD or Entra groups, DNS, backup, monitoring, patching, and a TLS reverse proxy or load balancer already exist. |
| High availability | 3 or more VMs | Future architecture | Requires replacing local SQLite with a client-server database and adding real auth/session handling. |

Current recommended VM count: **1 application VM**.

Reason: Access Register currently writes to a local SQLite file and runs one Python process. Multiple writable app VMs would create data consistency and file locking risk unless the persistence layer is redesigned.

## VM Specification

| Item | Pilot minimum | Recommended starting spec |
| --- | --- | --- |
| VM name | `AR-APP01` | `AR-APP01` |
| Guest OS | Windows Server 2022 Standard or Windows Server 2025 Standard | Windows Server 2025 Standard, domain joined |
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

Python 3.12 or newer is required. The app has no third-party Python dependencies.

## Network Specification

| Flow | Port | Source | Destination | Notes |
| --- | --- | --- | --- | --- |
| User access, pilot | TCP 8087 | Admin workstation subnet or internal reverse proxy | `AR-APP01` | Restrict by Windows Firewall and network ACLs. |
| User access, hardened target | TCP 443 | Internal users on LAN or VPN | Reverse proxy or load balancer | Terminate TLS before traffic reaches the app. |
| App health check | TCP 8087 | Monitoring host | `AR-APP01` | Check `/api/summary` or `/`. |
| Admin access | RDP 3389 or site remote admin tool | Admin workstation subnet | `AR-APP01` | Restrict to infrastructure admins. |
| Backup | Site-specific | Backup service | `AR-APP01` and backup repository | Back up the app folder, database, logs, and exported backups. |
| AD export input | SMB or protected copy path | AD export job host | `AR-APP01\D$\AccessRegister\import-drop` or approved share | Only if scheduled AD export is automated outside the app. |

The app defaults to `127.0.0.1:8087`. For VM access, bind to the VM interface only after the firewall is restricted:

```powershell
$env:ACCESS_REGISTER_HOST = "0.0.0.0"
$env:ACCESS_REGISTER_PORT = "8087"
$env:ACCESS_REGISTER_DB = "D:\AccessRegister\data\access_register.db"
python D:\AccessRegister\app\app.py
```

## User and Service Accounts

| Account or group | Type | Purpose | Required access |
| --- | --- | --- | --- |
| `DOMAIN\gmsa-ar-app$` | Group managed service account preferred | Runs the Access Register process | Read and execute app files. Modify `D:\AccessRegister\data` and `D:\AccessRegister\logs`. No local admin after install. |
| `DOMAIN\svc-ar-app` | Domain service account fallback | Runs the app if gMSA is not available | Same as gMSA. Password vaulted and rotated by policy. |
| `DOMAIN\gmsa-ar-adsync$` | Group managed service account preferred | Runs external AD export job, if used | Read only the AD attributes needed for sync. Write only to approved export drop. |
| `DOMAIN\AccessRegister-Admins` | AD or Entra group | Target Admin role | App administration, imports, AD sync, backups, auth settings. |
| `DOMAIN\AccessRegister-Reviewers` | AD or Entra group | Target Reviewer role | Reviews, request decisions, campaign completion. |
| `DOMAIN\AccessRegister-HR` | AD or Entra group | Target HR role | Employee and offboarding workflows. |
| `DOMAIN\AccessRegister-ReadOnly` | AD or Entra group | Target ReadOnly role | Inventory and evidence visibility without writes. |
| `DOMAIN\vSphere-AccessRegister-Ops` | vCenter group | Operates the VM | Least-privilege vSphere role scoped to the VM folder or resource pool. |
| `DOMAIN\vSphere-AccessRegister-Backup` | vCenter or backup role | Backup platform access | Backup and restore permissions scoped to the app VM. |
| Local break-glass admin | Local Windows account | Emergency access | Disabled or vaulted by policy, monitored, and excluded from daily use. |

Important: the AD or Entra role groups are target production mappings today. The current app stores these group names in Security settings but does not yet enforce login.

## File System Permissions

Apply NTFS permissions so the service account can run the app without broad server control:

| Path | Service account | App admins | Local administrators |
| --- | --- | --- | --- |
| `D:\AccessRegister\app` | Read and execute | Modify | Full control |
| `D:\AccessRegister\data` | Modify | Modify | Full control |
| `D:\AccessRegister\logs` | Modify | Modify | Full control |
| `D:\AccessRegister\import-drop` | Read, optional delete after import | Modify | Full control |

Do not grant `Everyone` or broad domain user groups access to the database, backups, logs, or saved AD export payloads.

## Install Procedure

1. Create `AR-APP01` from the approved Windows Server template.
2. Assign CPU, memory, OS disk, and data disk using the VM spec above.
3. Patch the OS, install VMware Tools, join the domain, and apply the server baseline.
4. Create `D:\AccessRegister` folders and NTFS permissions.
5. Install Python 3.12 or newer from an approved internal package source.
6. Copy the app files into `D:\AccessRegister\app`.
7. Set environment variables:

```powershell
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_HOST", "0.0.0.0", "Machine")
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_PORT", "8087", "Machine")
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_DB", "D:\AccessRegister\data\access_register.db", "Machine")
```

8. Create a run script owned by administrators, for example `D:\AccessRegister\run-access-register.ps1`:

```powershell
Set-Location "D:\AccessRegister\app"
& "C:\Program Files\Python312\python.exe" "app.py" *> "D:\AccessRegister\logs\access-register.log"
```

9. Register the script as a Windows scheduled task or approved service wrapper under the app service account.
10. Create a Windows Firewall inbound rule for TCP 8087 that allows only the approved source subnet or reverse proxy.
11. Start the task or service and confirm:

```powershell
Invoke-WebRequest http://127.0.0.1:8087/api/summary
```

12. Open the UI from an allowed workstation and complete the smoke workflow in `AGENTS.md`.

## Backup and Recovery

The app has an in-app backup action that copies the SQLite database into `D:\AccessRegister\data\backups` and records the run in `backup_runs`. Use it for operator-triggered evidence backups, but do not rely on it as the only recovery control.

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
cd D:\AccessRegister\app
python -m unittest discover -s tests
python -m unittest tests.test_ui_smoke
Invoke-WebRequest http://127.0.0.1:8087/api/summary
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

Before Access Register becomes an authoritative production access-control system, close these gaps:

- Replace the local role selector with real AD or Entra login.
- Enforce authentication on every read and write route.
- Derive actor and role on the server, not from client-supplied headers.
- Terminate TLS and set secure browser/session controls.
- Decide whether SQLite remains acceptable or migrate to a managed database.
- Replace saved AD export replay with a secure connector or controlled service account job.
- Store connector secrets outside SQLite.
- Forward audit logs to protected central logging.
- Define retention for audit logs, imports, AD exports, removal evidence, and backups.

## References

Broadcom. "vSphere Virtual Machine Administration." *VMware vSphere Documentation*, https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-virtual-machine-administration.html. Accessed 28 June 2026.

Broadcom. "vSphere Security." *VMware vSphere Documentation*, https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-security.html. Accessed 28 June 2026.

Microsoft. "Hardware Requirements for Windows Server." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/get-started/hardware-requirements. Accessed 28 June 2026.

Microsoft. "Group Managed Service Accounts Overview." *Microsoft Learn*, https://learn.microsoft.com/en-us/windows-server/security/group-managed-service-accounts/group-managed-service-accounts-overview. Accessed 28 June 2026.

Python Software Foundation. "venv - Creation of Virtual Environments." *Python 3 Documentation*, https://docs.python.org/3/library/venv.html. Accessed 28 June 2026.

SQLite Consortium. "Online Backup API." *SQLite Documentation*, https://www.sqlite.org/backup.html. Accessed 28 June 2026.
