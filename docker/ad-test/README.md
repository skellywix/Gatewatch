# Gatewatch Docker AD Test Lab

This directory defines a local Samba Active Directory domain controller for Gatewatch testing.

The lab is intentionally local-only:

- Realm: `GATEWATCH.TEST`
- NetBIOS domain: `GATEWATCH`
- Container: `gatewatch-ad-test`
- Docker network: `gatewatch-lab`
- Persistent volumes: `gatewatch-ad-lib`, `gatewatch-ad-cache`, `gatewatch-ad-log`
- No AD ports are published to the Windows host.

Build:

```powershell
docker build -t gatewatch-ad-test:codex docker/ad-test
```

Export seeded AD users as Gatewatch-compatible CSV:

```powershell
docker exec gatewatch-ad-test export-gatewatch-ad
```

Sync the seeded AD users into the permanent Gatewatch Docker test environment:

```powershell
powershell -ExecutionPolicy Bypass -File docker/ad-test/sync-gatewatch-ad.ps1
```

The script expects:

- Gatewatch test app: `http://127.0.0.1:18099`
- AD container: `gatewatch-ad-test`
- Local test role header: `Admin`

Override defaults when needed:

```powershell
$env:GATEWATCH_TEST_URL="http://127.0.0.1:18099"
$env:GATEWATCH_AD_CONTAINER="gatewatch-ad-test"
$env:GATEWATCH_SYNC_ACTOR="Docker AD Sync"
powershell -ExecutionPolicy Bypass -File docker/ad-test/sync-gatewatch-ad.ps1 -Json
```

Route disabled-user access into removals after a sync only when the test needs that workflow:

```powershell
powershell -ExecutionPolicy Bypass -File docker/ad-test/sync-gatewatch-ad.ps1 -RouteDisabledAccess
```

Seeded users:

- `gw.admin`
- `gw.ops`
- `gw.people`
- `gw.compliance`
- `gw.audit`
- `gw.employee`
- `gw.disabled`
- `svc.gatewatch.adsync`

Seeded production-style groups:

- `AccessRegister-Admins`: `gw.admin`, `svc.gatewatch.adsync`
- Authenticated non-admin test users exercise the app's User role.

The lab currently runs with Docker `--privileged` because Samba AD DC provisioning needs filesystem ACL/xattr behavior for SYSVOL. Keep the container on the isolated `gatewatch-lab` network and do not publish AD ports unless a test explicitly requires it.
