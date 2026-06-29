# Gatewatch Production Checklist

Last reviewed: 2026-06-29

This is the simple install-day checklist for putting Gatewatch into a production-style internal pilot. It assumes the current recommended path:

- One internal vSphere VM.
- Docker Compose running the Gatewatch container.
- SQLite stored in the `gatewatch-data` Docker volume.
- Users reach Gatewatch only through an AD-authenticated TLS reverse proxy.
- The app runs in `trusted_proxy` mode, not local role-selector mode.

Use this checklist with the deeper references:

- `docs/vsphere-deployment.md` for sizing, network, accounts, and gaps.
- `docker/vsphere/README.md` for the Docker Compose profile.
- `docs/on-prem-docker-ad-sso.md` for the reverse proxy and AD SSO contract.
- `docs/vsphere-technician-runbook.md` only if the site chooses the native Windows scheduled-task fallback instead of Docker.

## Fast Path Script

Download the public GitHub repository to the VM desktop, open the downloaded folder, then double-click:

```text
Deploy-Gatewatch.cmd
```

That is the one-click path. The launcher self-elevates, copies the downloaded files into `D:\AccessRegister\app` or `C:\AccessRegister\app`, then runs the production installer from that install folder. The installer installs or verifies Git, OpenSSH when private-repo deploy-key mode is used, Docker, and Docker Compose. It then prompts for the production URL, AD group mappings, reverse-proxy location, proxy secret choice, and optional AD sync scheduled-task details. Each prompt tells you where to get the value.

For a laptop proof test, enter `http://localhost:8087` as the production URL and keep the app bound to `127.0.0.1`. The installer will automatically use local role-selector auth for that loopback-only test so the browser UI works without an AD reverse proxy. Do not use `local` auth for a production or LAN-exposed deployment.

For a fully automatic dependency bootstrap, use a Windows 10/11 Pro or Enterprise VM with desktop access. On that host shape, the script can install Git using winget or the current Git for Windows release, install WSL support when needed, download Docker Desktop from Docker's official HTTPS installer, start Docker Desktop, and wait for both the Docker engine and `docker compose version`.

Do not use Docker Desktop as the production runtime on Windows Server. Docker's Windows installation docs state that Docker Desktop is not supported on Windows Server. If the production VM must be Windows Server, install a site-approved Linux-container runtime first, or pass that installer explicitly with `-DockerInstaller` and `-DockerInstallerArguments`.

The default GitHub repo is public and does not need a deploy key:

```text
https://github.com/skellywix/Gatewatch.git
```

If you prefer a single-script bootstrap instead of downloading the full folder first, copy `scripts\install-gatewatch-production.ps1` to the VM and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Temp\install-gatewatch-production.ps1
```

That script installs or checks Git, OpenSSH when needed, Docker, and Docker Compose. It fetches the app from GitHub, writes `docker\vsphere\.env`, starts the Docker Compose profile, checks `/healthz`, and writes a non-secret handoff file at `docker\vsphere\deployment-handoff.txt`.

If the repo is made private again later, run the script with `-PrivateGitHubRepo`. It will generate an Ed25519 deploy key under `D:\AccessRegister\keys` or `C:\AccessRegister\keys`, print the public key, and pause. Add that public key as a read-only deploy key here:

```text
https://github.com/skellywix/Gatewatch/settings/keys
```

Then press Enter in the script and it will clone the repo.

Use prepare-only mode if you want the env file and handoff before starting the container:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Temp\install-gatewatch-production.ps1 -SkipStart
```

Use explicit parameters when you already know the values:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Temp\install-gatewatch-production.ps1 `
  -GatewatchUrl "https://gatewatch.company.local" `
  -AdminGroups "COMPANY\Gatewatch-Admins" `
  -SupervisorGroups "COMPANY\Gatewatch-Supervisors" `
  -ReviewerGroups "COMPANY\Gatewatch-Reviewers" `
  -HrGroups "COMPANY\Gatewatch-HR" `
  -ReadOnlyGroups "COMPANY\Gatewatch-ReadOnly" `
  -AuthMode trusted_proxy `
  -RunVerification
```

If the server already has GitHub access through Git Credential Manager, GitHub CLI, or a preconfigured SSH key, run with `-UseExistingGitAuth`. Public installs do not need that flag.

Dependency behavior:

- Git: installs with `winget` when available, otherwise downloads the latest Git for Windows installer from GitHub releases.
- OpenSSH: installed automatically only when `-PrivateGitHubRepo` requires a deploy key and `ssh-keygen` is missing.
- Docker on Windows 10/11 Pro or Enterprise: downloads Docker Desktop from Docker's official installer URL, installs with command-line arguments, starts Docker Desktop, and waits for both the Docker engine and Docker Compose.
- Docker on Windows Server: requires a preapproved Linux-container runtime. Pass its installer path or HTTPS URL with `-DockerInstaller`; the script rejects plain HTTP installer downloads.

Official dependency references:

- Docker Desktop Windows install: <https://docs.docker.com/desktop/setup/install/windows-install/>
- Git for Windows releases: <https://github.com/git-for-windows/git/releases>
- Windows OpenSSH install: <https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse>

The script does not create DNS, TLS certificates, the AD SSO reverse proxy, or enterprise backup policy. It tells you what those systems still need and records the handoff steps.

## Production Rules

- [ ] Do not expose the app container directly to the LAN.
- [ ] Do not run production with `ACCESS_REGISTER_AUTH_MODE=local`.
- [ ] Do not set `ACCESS_REGISTER_ALLOW_INSECURE_LOCAL_NETWORK=1` for production.
- [ ] Keep `GATEWATCH_BIND_ADDRESS=127.0.0.1` unless a separate reverse proxy host needs access and the VM firewall allows only that proxy.
- [ ] Treat the SQLite database, backups, saved AD exports, audit event log, and real `.env` file as sensitive company data.
- [ ] Keep the production `.env` file out of Git.

## 1. Confirm the Target

- [ ] Pick the production DNS name, for example `gatewatch.company.local`.
- [ ] Pick the vSphere VM name, for example `AR-APP01`.
- [ ] Pick the reverse proxy host. Same VM is simplest for the pilot.
- [ ] Confirm the VM will be reachable only on internal LAN or VPN.
- [ ] Confirm the app backend port is not user-facing. Default is `127.0.0.1:8087`.
- [ ] Confirm the deployment is a single writable app instance. Do not run active-active app containers against the same SQLite database.

## 2. Prepare AD Groups and Accounts

- [ ] Create or confirm the Gatewatch role groups:

```text
DOMAIN\AccessRegister-Admins
DOMAIN\AccessRegister-Supervisors
DOMAIN\AccessRegister-Reviewers
DOMAIN\AccessRegister-HR
DOMAIN\AccessRegister-ReadOnly
```

- [ ] Add the first production admin user to `DOMAIN\AccessRegister-Admins`.
- [ ] Create or choose the AD sync service account or gMSA.
- [ ] Give the AD sync account read-only access to the approved AD user attributes.
- [ ] Add the AD sync account to the Gatewatch Admin mapping group, or to a narrower group that maps to Admin for imports.
- [ ] Confirm a service owner is responsible for backups, log shipping, and restore tests.

## 3. Prepare the vSphere VM

- [ ] Create one VM from the approved server template.
- [ ] Start with this sizing unless the deployment spec says otherwise:

```text
4 vCPU
8 GB RAM
100 GB OS disk
100 GB data disk
1 internal VMXNET3 adapter
```

- [ ] Patch the OS.
- [ ] Install VMware Tools.
- [ ] Join the domain if required by site policy.
- [ ] Apply EDR or antivirus.
- [ ] Enable the host firewall.
- [ ] Confirm time sync follows the site standard.
- [ ] Install Docker Engine and the Docker Compose plugin from the approved internal source.
- [ ] Verify Docker works:

```powershell
docker version
docker compose version
```

## 4. Put the App on the VM

- [ ] Create the deployment folder:

```powershell
New-Item -ItemType Directory -Force -Path "D:\AccessRegister\app" | Out-Null
```

- [ ] Copy or clone the approved Gatewatch release into:

```text
D:\AccessRegister\app
```

- [ ] Confirm these files exist:

```powershell
Test-Path "D:\AccessRegister\app\app.py"
Test-Path "D:\AccessRegister\app\Dockerfile"
Test-Path "D:\AccessRegister\app\docker\vsphere\compose.yaml"
Test-Path "D:\AccessRegister\app\docker\vsphere\.env.example"
```

## 5. Run the Preflight Checks

Run this gate against the exact release being deployed. Use the build workstation, or the VM if Python is installed there. Docker production hosting does not require host Python after the image is built.

```powershell
cd D:\AccessRegister\app
python scripts\verify.py --list
python scripts\verify.py
```

- [ ] Confirm Python compile passes.
- [ ] Confirm backend and UI smoke tests pass.
- [ ] Confirm JavaScript syntax check passes if Node.js is installed.
- [ ] If Docker is available on the build workstation or VM, run the Docker gate:

```powershell
python scripts\verify.py --docker
```

## 6. Create the Production Docker Environment

- [ ] Copy the example environment file:

```powershell
cd D:\AccessRegister\app
Copy-Item docker\vsphere\.env.example docker\vsphere\.env
```

- [ ] Generate a long proxy-only secret:

```powershell
$Bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
[Convert]::ToBase64String($Bytes)
```

- [ ] Edit the real `.env` file:

```powershell
notepad docker\vsphere\.env
```

- [ ] Set `ACCESS_REGISTER_PROXY_SECRET` to the generated value.
- [ ] Set `ACCESS_REGISTER_ADMIN_GROUPS` to the production Admin group.
- [ ] Set the Supervisor, Reviewer, HR, and ReadOnly group values.
- [ ] Keep `ACCESS_REGISTER_AUDIT_EVENT_LOG=/data/audit-events.jsonl`.
- [ ] Keep `ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED=0` until log shipping is proven.
- [ ] Keep `GATEWATCH_BIND_ADDRESS=127.0.0.1` when the reverse proxy runs on the same VM.
- [ ] Confirm Compose accepts the environment:

```powershell
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml config --quiet
```

## 7. Start Gatewatch

Run from the repository root:

```powershell
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml up -d --build
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml ps
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml logs --tail 100 app
```

- [ ] Confirm the app container is running.
- [ ] Confirm the container health check is healthy or starting.
- [ ] Confirm there are no startup errors about `ACCESS_REGISTER_PROXY_SECRET`.
- [ ] Confirm there are no database permission errors.

Run a local health check from the VM:

```powershell
$Port = "8087"
Invoke-RestMethod "http://127.0.0.1:$Port/healthz"
```

- [ ] Confirm the response reports service and database health.

## 8. Configure the Reverse Proxy

- [ ] Create internal DNS for the production hostname.
- [ ] Install an internal TLS certificate trusted by domain machines.
- [ ] Configure AD SSO, AD FS, Entra ID, or another approved identity provider at the proxy.
- [ ] Configure the proxy to strip inbound identity headers from clients.
- [ ] Configure the proxy to inject authenticated identity headers:

```text
X-Remote-User
X-Remote-Email
X-Remote-Name
X-Remote-Groups
```

- [ ] Configure the proxy to inject the shared secret header:

```text
X-Access-Register-Proxy-Secret
```

- [ ] Set the header value to the same `ACCESS_REGISTER_PROXY_SECRET` from `docker\vsphere\.env`.
- [ ] Forward traffic from `https://gatewatch.company.local` to `http://127.0.0.1:8087` when the proxy runs on the same VM.
- [ ] Confirm the VM firewall allows user traffic only to the proxy listener, normally TCP 443.
- [ ] Confirm TCP 8087 is not open to user subnets.

## 9. First Login and App Security Setup

- [ ] Open the production URL from an allowed workstation:

```text
https://gatewatch.company.local
```

- [ ] Log in as a user in the Admin group.
- [ ] Open Security.
- [ ] Set the identity provider name, such as Active Directory or Microsoft Entra ID.
- [ ] Enable the setting that requires real login when the provider is wired.
- [ ] Confirm the Admin, Supervisor, Reviewer, HR, and ReadOnly group mappings.
- [ ] Save the settings.
- [ ] Log in with a non-admin test user and confirm the role matches their AD group.

## 10. Configure Production AD Sync

Run the sync from a domain-joined Windows host with the ActiveDirectory PowerShell module installed.

- [ ] Confirm the sync account can read the approved AD attributes.
- [ ] Confirm the sync account can authenticate through the reverse proxy.
- [ ] Create the scheduled task command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "D:\AccessRegister\app\scripts\sync-active-directory.ps1" `
  -GatewatchUrl "https://gatewatch.company.local" `
  -SearchBase "OU=Users,DC=company,DC=local" `
  -UseDefaultCredentialsForSso `
  -Json
```

- [ ] Run the command once manually.
- [ ] Confirm new directory users appear in Gatewatch.
- [ ] Confirm disabled AD users are flagged for review, not deleted.
- [ ] Register the command as a scheduled task under the approved sync account or gMSA.
- [ ] Confirm the scheduled task history shows success.

## 11. Configure Backups and Log Shipping

- [ ] Confirm the Docker volume exists:

```powershell
docker volume inspect gatewatch-data
```

- [ ] Confirm infrastructure backup includes the VM and the `gatewatch-data` volume.
- [ ] Configure the approved log shipper or SIEM agent to collect:

```text
/data/audit-events.jsonl
```

- [ ] After log shipping is proven, decide whether to set `ACCESS_REGISTER_AUDIT_EVENT_LOG_REQUIRED=1`.
- [ ] Run an in-app backup from Governance.
- [ ] Confirm backup files appear under `/data/backups` inside the container volume.
- [ ] Record the retention policy for database backups, audit logs, imports, AD exports, and removal evidence.
- [ ] Schedule the first restore test.

## 12. Complete the Smoke Test

Use the production URL and a real Admin or test Admin account.

- [ ] Open the dashboard.
- [ ] Inspect an employee from the access inventory table.
- [ ] Certify a stale record from Reviews.
- [ ] Mark a terminated employee removal item complete with evidence.
- [ ] Import the sample CSV from Imports and confirm unmatched accounts increase.
- [ ] Run AD sync and confirm new users plus disabled-directory flags appear.
- [ ] Edit an employee, enable manual override, sync AD again, and confirm protected local fields remain.
- [ ] Create an access request.
- [ ] Approve the access request and confirm the created access record keeps the expiration date.
- [ ] Route disabled-user access from Risk Center and confirm it moves to removal pending.
- [ ] Create a review campaign from Governance and mark it complete.
- [ ] Add a shared account and a physical credential from Assets.
- [ ] Add a connector plan and update Security authentication settings.
- [ ] Run a backup from Governance and confirm the backup run appears.
- [ ] Check Audit Log for the recorded actions.

## 13. Capture Handoff Evidence

Save these outputs in the deployment ticket:

```powershell
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml ps
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml logs --tail 100 app
Invoke-RestMethod "http://127.0.0.1:8087/healthz"
docker volume inspect gatewatch-data
```

- [ ] Attach a screenshot of the Gatewatch dashboard.
- [ ] Attach a screenshot of the Security group mappings.
- [ ] Attach the result of `python scripts\verify.py` from the build workstation or VM.
- [ ] Attach the result of the AD sync scheduled task test.
- [ ] Attach confirmation that the VM and data volume are in backup scope.
- [ ] Attach confirmation that audit event log shipping is working, or record it as an open production gap.

## 14. Restart Test

- [ ] Restart the VM during the approved maintenance window.
- [ ] Confirm the container returns automatically:

```powershell
docker compose --env-file D:\AccessRegister\app\docker\vsphere\.env -f D:\AccessRegister\app\docker\vsphere\compose.yaml ps
Invoke-RestMethod "http://127.0.0.1:8087/healthz"
```

- [ ] Open `https://gatewatch.company.local` and confirm login still works.

## 15. Rollback Plan

Use rollback only when the deployment must be backed out.

- [ ] Stop the container:

```powershell
cd D:\AccessRegister\app
docker compose --env-file docker\vsphere\.env -f docker\vsphere\compose.yaml down
```

- [ ] Preserve the Docker volume, database, audit event log, backups, and container logs unless the data owner approves deletion.
- [ ] Disable the reverse proxy site or remove the DNS record if users must be blocked.
- [ ] Disable the AD sync scheduled task.
- [ ] Record the rollback reason and evidence location in the deployment ticket.

## Production Done

Gatewatch is ready for the internal pilot when all items below are checked:

- [ ] App is reachable only at the production HTTPS URL.
- [ ] Direct container access is blocked from user networks.
- [ ] `trusted_proxy` mode is active.
- [ ] The proxy strips inbound identity headers and injects trusted identity headers.
- [ ] `ACCESS_REGISTER_PROXY_SECRET` is configured on both proxy and app.
- [ ] Admin group bootstrap works.
- [ ] Role mappings are saved and verified with test users.
- [ ] AD sync has run successfully.
- [ ] Backups are configured and the first in-app backup is complete.
- [ ] Audit event logging is configured, with any log-shipping gap documented.
- [ ] Smoke test is complete.
- [ ] Restart test is complete.
- [ ] Deployment evidence is attached to the ticket.
