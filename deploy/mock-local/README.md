# Gatewatch Local Mock Deployment

This package rehearses a complete local Gatewatch deployment from the GitHub source archive. It is intentionally separate from production rollout scripts so the mock path can be built, checked, and torn down without touching a service host.

Defaults:

- Source: `https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz`
- URL: `http://127.0.0.1:18087`
- Container: `gatewatch-mock`
- Image: `gatewatch-mock:latest`
- Volume: `gatewatch-mock-data`
- Temporary build directory: `output/mock-deployment`

## Mock Deployment Checklist

- [ ] Confirm the checkout is clean enough for testing.
- [ ] Run the app verification suite.
- [ ] Inspect this reusable package.
- [ ] Build and run the mock container from the GitHub source archive.
- [ ] Check `/healthz` over HTTP.
- [ ] Confirm Docker reports the container health check as healthy.
- [ ] Tear down the mock container, image, and data volume.
- [ ] Verify the transient build directory, container, image, and volume are gone.

## PowerShell Runbook

Run these commands from the repository root:

```powershell
git status --short --branch
python scripts\verify.py
python scripts\verify.py --docker
python deploy\mock-local\mock_deploy.py inspect-package
python deploy\mock-local\mock_deploy.py deploy --reset-data
python deploy\mock-local\mock_deploy.py health
Invoke-RestMethod http://127.0.0.1:18087/healthz
python deploy\mock-local\mock_deploy.py teardown
python deploy\mock-local\mock_deploy.py teardown --verify-only
```

The `deploy` command downloads the GitHub source archive, inspects the required app files, builds the Docker image, starts a locked-down local container, waits for HTTP `/healthz`, waits for Docker health to become healthy, then removes the temporary source directory.

The `teardown` command removes the mock container, Docker image, and Docker volume, deletes the temporary build directory when present, and verifies those runtime artifacts are gone. It leaves the reusable package files in `deploy/mock-local`.

## Bash Runbook

```bash
git status --short --branch
python3 scripts/verify.py
python3 scripts/verify.py --docker
python3 deploy/mock-local/mock_deploy.py inspect-package
python3 deploy/mock-local/mock_deploy.py deploy --reset-data
python3 deploy/mock-local/mock_deploy.py health
curl -fsS http://127.0.0.1:18087/healthz
python3 deploy/mock-local/mock_deploy.py teardown
python3 deploy/mock-local/mock_deploy.py teardown --verify-only
```

## Options

Use a different branch or fork archive when rehearsing a release candidate:

```powershell
python deploy\mock-local\mock_deploy.py deploy --source-url https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz
```

Use another local port if `18087` is busy:

```powershell
python deploy\mock-local\mock_deploy.py deploy --port 18088 --reset-data
python deploy\mock-local\mock_deploy.py health --port 18088
python deploy\mock-local\mock_deploy.py teardown --port 18088
```

Use `--keep-data` only when you intentionally want the mock SQLite volume to survive a redeploy. Use `teardown --keep-image` only when you intentionally want to keep the built Docker image for inspection. The normal evidence path uses `--reset-data` and ends with `teardown` so no mock runtime data remains.

## Expected Evidence

The deployment proof should include:

- `python scripts\verify.py`
- `python scripts\verify.py --docker`
- `python deploy\mock-local\mock_deploy.py inspect-package`
- `python deploy\mock-local\mock_deploy.py deploy --reset-data`
- `python deploy\mock-local\mock_deploy.py health`
- `Invoke-RestMethod http://127.0.0.1:18087/healthz`
- `python deploy\mock-local\mock_deploy.py teardown`
- `python deploy\mock-local\mock_deploy.py teardown --verify-only`

Do not put secrets into this mock package. The helper generates a temporary session secret for the container and never writes it to disk.
