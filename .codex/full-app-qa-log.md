# Gatewatch Full Application QA Log

Branch: `codex/full-app-qa`
Base: `origin/main` at `25de23f04d17b6c45906820ceacd05540adb746c`

## Baseline Discovery

Reviewed:

- `AGENTS.md`
- `README.md`
- `CODEBASE_NOTES.md`
- `docs/ROLLOUT.md`
- `.github/workflows/ci.yml`
- `scripts/verify.py`
- `tests/test_app.py`
- `tests/test_deployment.py`
- `tests/test_verify_script.py`
- `tests/frontend-monitor.test.js`
- `docker/full-test/.env.example`
- `docker/full-test/README.md`
- `deploy/reverse-proxy/oauth2-proxy-gatewatch.env.example`

Repository/tooling facts:

- No package manager manifest or lockfile is present.
- Python is the primary runtime and uses only the standard library.
- Node is optional for frontend syntax and monitor regression checks.
- Docker is optional for production image and trusted-proxy full-test validation.
- CI runs `python scripts/verify.py --docker` on pull requests and pushes to `main`.

Commands:

- `git fetch origin --prune`: passed.
- `python --version`: Python 3.12.10.
- `node --version`: v22.22.3.
- `docker --version`: Docker 29.5.3.
- `python scripts\verify.py --list`: passed, discovered Python compile, unittest discovery, `node --check`, and Node monitor regression; Docker and full-test lab are opt-in.

## Section 19: Security and Privacy

Scope inspected:

- Trusted-proxy backend auth parsing and startup guard in `app.py`.
- New Ubuntu installer trusted-proxy flags in `scripts/install-ubuntu.sh`.
- New Nginx/OAuth2 Proxy deployment bundle under `deploy/reverse-proxy/`.
- Docs covering direct spoofing rejection, proxy-secret handling, and secret file privacy.

Issue fixed:

- `--validate-paths-only` exited before the newly added trusted-proxy installer option validation, so CI-safe installer validation could not prove missing or weak proxy secrets are rejected before privileged install work. The exit now occurs after non-privileged service, host, port, Entra, and trusted-proxy config validation.

Tests added/updated:

- Added `test_ubuntu_installer_validates_trusted_proxy_config_before_privileged_file_operations`.
- Extended deployment assertions for reverse-proxy bundle wiring and Alpine Dockerfile hardening.

Security notes:

- Gatewatch still requires `GATEWATCH_PROXY_SECRET` in `trusted_proxy` mode.
- The Nginx bundle overwrites client-controlled identity headers before injecting trusted `X-Remote-*` headers and the proxy secret.
- Direct spoofing without the shared secret is documented and covered by existing trusted-proxy tests.
- No raw session, Entra client, or proxy secret values are echoed by app diagnostics.

Commands:

- `python -m unittest tests.test_deployment`: passed, 13 tests, 2 Windows-local skips.
- `python -m py_compile app.py scripts\verify.py tests\test_deployment.py`: passed.
- `node --check web\app.js`: passed.
- `python scripts\verify.py --docker --docker-full-test`: passed, 7 checks, 53 backend/UI tests with 2 Windows-local skips, 8 frontend monitor tests, Docker image build, Compose config, and trusted-proxy browser SSO smoke.

## Section 21: CI/CD and Release Readiness

Scope inspected:

- `.github/workflows/ci.yml`
- `scripts/verify.py`
- `Dockerfile`
- `docs/ROLLOUT.md`
- `deploy/reverse-proxy/`
- `scripts/install-ubuntu.sh`

Improvements covered:

- Production image uses `python:3.12-alpine`, runs as a non-root `gatewatch` user, and removes unused `pip` runtime files.
- Rollout docs now include production reverse-proxy verification, trust-boundary curl checks, and rollback steps.
- Ubuntu installer can configure trusted-proxy mode and persists `GATEWATCH_AUTH_MODE` plus `GATEWATCH_PROXY_SECRET`.

Validation:

- Full local release gate passed with Docker and trusted-proxy browser SSO lab.

## Section 10: State Management and Cache

Scope inspected:

- Theme state initialization and persistence in `web/app.js`.
- Frontend monitor regression harness in `tests/frontend-monitor.test.js`.

Issue fixed:

- Theme persistence no longer depends on helper calls that first check for `typeof localStorage`; direct storage reads/writes are wrapped in `try`/`catch`, so storage-denied browsers still render and update the current page theme.

Tests added/updated:

- Added `theme state works when browser storage is unavailable`, which runs the app VM without `localStorage`, verifies the default light theme, and confirms switching to dark does not throw.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 9 tests.

## Section 2: Navigation and Routing

Scope inspected:

- Static tab markup in `web/index.html`.
- Tab click, keyboard navigation, hash routing, and hidden admin-tab routing in `web/app.js`.
- Existing frontend monitor tab tests in `tests/frontend-monitor.test.js`.

Tests added/updated:

- Added `hash routing preserves allowed tabs and rejects hidden admin routes`, which starts on `#activity`, switches tabs through `setActiveTab`, simulates `hashchange` back/navigation into `#templates`, and confirms a direct `#backend` route falls back to Overview for non-admin users.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 10 tests.

## Section 3: Authentication and Authorization

Scope inspected:

- Session-derived admin, supervisor, and viewer permissions in `app.py`.
- Trusted-proxy secret validation and group-to-role mapping in `app.py`.
- HTTP auth/authorization tests in `tests/test_app.py`.

Tests added/updated:

- Extended `test_trusted_proxy_auth_uses_ad_group_headers_for_admin_actions` to prove direct identity spoofing with `X-Remote-*` headers but no `X-Gatewatch-Proxy-Secret` is rejected with `403`.

Commands:

- `python -m unittest tests.test_app.HttpTests.test_trusted_proxy_auth_uses_ad_group_headers_for_admin_actions`: passed.
- `python -m py_compile app.py tests\test_app.py`: passed.

## Section 4: Forms and Validation

Scope inspected:

- Employee form payload validation in `Store.employee_payload`.
- Employee create HTTP route in `app.py`.
- Existing frontend form serialization and backend validation coverage.

Tests added/updated:

- Added `test_http_employee_form_validation_errors_do_not_mutate_records`, covering invalid email and missing required name submissions through `/api/employees`, and proving rejected form submissions leave the employee table unchanged.

Commands:

- `python -m unittest tests.test_app.HttpTests.test_http_employee_form_validation_errors_do_not_mutate_records`: passed.
- `python -m py_compile app.py tests\test_app.py`: passed.

## Remaining Sections

Not yet completed in this branch:

5. Buttons, controls, overlays, and interactive states
6. Loading, empty, error, and success states
7. API integration and data fetching
8. Backend API behavior
9. Database/persistence/migrations, if present
11. Tables, search, filters, and pagination
12. File/media flows, if present
13. Payments/billing, if present
14. Admin/RBAC, if present
15. Accessibility
16. Responsive/cross-browser behavior
17. Motion/effects/reduced-motion behavior
18. Performance and bundle health
20. Analytics/observability, if present
22. Final e2e regression
