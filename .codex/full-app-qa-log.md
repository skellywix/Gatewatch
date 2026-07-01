# Gatewatch Full Application QA Log

Branch: `codex/full-app-qa`
Base: `origin/main` at `8824b2117e72a8b59261f8e0ff91261ee84d8c38`

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

## Section 5: Buttons, Controls, Overlays, and Interactive States

Scope inspected:

- User action button state handling in `updateFormState`.
- Existing disabled-button, chip, tab, and reduced-motion CSS assertions.
- Frontend monitor tests for tab controls, theme controls, and selected-user state.

Tests added/updated:

- Added `user action buttons reflect selection and permission state`, covering disabled action buttons with no selected user, non-admin selected-user controls, admin delete enablement, delete tooltip text, and save button mode text.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 11 tests.

## Section 6: Loading, Empty, Error, and Success States

Scope inspected:

- Busy-state rendering for metrics, monitoring, and activity regions in `web/app.js`.
- Empty-state copy for overview, user list, and activity feed surfaces.
- Toast success and error feedback styling in `web/app.js`.
- Frontend monitor VM harness coverage in `tests/frontend-monitor.test.js`.

Tests added/updated:

- Added `loading, empty, error, and success states stay visible`, covering loading `aria-busy` flags, no-data empty copy, filtered-empty copy, success toast class state, and error toast class state.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 12 tests.
- `python scripts\verify.py`: passed, 54 backend/UI tests with 2 Windows-local skips and 12 frontend monitor tests.

## Section 7: API Integration and Data Fetching

Scope inspected:

- Frontend bootstrap fetch/hydration flow in `loadAll`.
- Shared `api` wrapper request defaults and error propagation.
- Existing backend `/api/bootstrap` HTTP coverage in `tests/test_app.py`.
- Frontend monitor VM harness mock-fetch behavior in `tests/frontend-monitor.test.js`.

Tests added/updated:

- Added `bootstrap data fetch hydrates state and reports API failures`, covering `/api/bootstrap` request defaults, bootstrap state hydration, hash-tab preservation after load, first-record selection, rendered profile output, success toast feedback, and failing API response handling.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 13 tests.
- `python scripts\verify.py`: passed, 54 backend/UI tests with 2 Windows-local skips and 13 frontend monitor tests.

## Section 8: Backend API Behavior

Scope inspected:

- API route dispatcher and JSON error handling in `app.py`.
- Request JSON parsing for API mutation routes.
- Employee create conflict handling and SQLite mutation boundary.
- Existing HTTP route coverage in `tests/test_app.py`.

Tests added/updated:

- Added `test_api_error_contracts_return_json_without_extra_mutation`, covering unknown API route JSON `404`, non-object JSON body rejection, duplicate employee conflict response, and unchanged employee count after rejected requests.

Commands:

- `python -m unittest tests.test_app.HttpTests.test_api_error_contracts_return_json_without_extra_mutation`: passed.
- `python scripts\verify.py`: passed, 55 backend/UI tests with 2 Windows-local skips and 13 frontend monitor tests.

## Section 9: Database, Persistence, and Migrations

Scope inspected:

- SQLite initialization and legacy employee status-check migration in `Store.init`.
- Employee column backfill logic in `_migrate_employee_columns`.
- Index creation in `_ensure_employee_indexes`.
- Default access-field seeding idempotency.

Tests added/updated:

- Extended `test_disabled_status_and_legacy_status_check_migration` to verify migrated legacy databases gain newer employee columns, expected indexes, seeded access fields, and idempotent re-initialization without duplicate seed rows.

Commands:

- `python -m unittest tests.test_app.StoreTests.test_disabled_status_and_legacy_status_check_migration`: passed.
- `python scripts\verify.py`: passed, 55 backend/UI tests with 2 Windows-local skips and 13 frontend monitor tests.

## Section 11: Tables, Search, Filters, and Pagination

Scope inspected:

- Overview status filter chips, counts, and search state in `web/app.js`.
- Users-tab search, datalist options, exact-match selection, and rendered profile list.
- Static HTML/CSS for search controls and list layout.
- Existing app surface; no pagination controls are present in the current Gatewatch UI.

Tests added/updated:

- Added `user search and status filter controls drive rendered lists`, covering user-search datalist options, user-search input filtering, exact email selection, status-filter click handling, and ignored disabled status filters.

Commands:

- `node --check web\app.js`: passed.
- `node --test tests\frontend-monitor.test.js`: passed, 14 tests.
- `python scripts\verify.py`: passed, 55 backend/UI tests with 2 Windows-local skips and 14 frontend monitor tests.

## Section 12: File and Media Flows

Scope inspected:

- Search for uploads, media handling, attachments, multipart parsing, downloads, and CSV export paths.
- Audit CSV export through `Store.audit_log_csv` and `/api/audit-log.csv`.
- Activity export link in `web/index.html`.

Result:

- No upload, media, attachment, or multipart ingestion flow is present in the current Gatewatch app.
- The only file-style user flow is the admin-only audit CSV export, which is covered by CSV escaping, authz, and HTTP response tests.

Tests added/updated:

- Added `test_audit_csv_export_returns_text_csv_and_escapes_formula_cells`, covering admin CSV download response status, `text/csv` content type, CSV row shape, and formula-safe actor escaping through the HTTP export route.

Commands:

- `rg -n "upload|file|media|csv|download|attachment|multipart|Content-Disposition" app.py web tests scripts README.md docker .github`: inspected; no upload/media ingestion flow found.
- `python -m unittest tests.test_app.StoreTests.test_search_summary_sqlite_pragmas_and_audit_csv`: passed.
- `python -m unittest tests.test_app.HttpTests.test_audit_csv_export_returns_text_csv_and_escapes_formula_cells`: passed.

## Section 13: Payments and Billing

Scope inspected:

- Search for payment, billing, checkout, subscription, invoice, Stripe, and price surfaces.

Result:

- No payment or billing flow is present in Gatewatch.

Commands:

- `rg -n "stripe|payment|billing|invoice|subscription|checkout|price" app.py web tests scripts README.md docker .github`: inspected; only security header `payment=()` policy found.

## Section 14: Admin and RBAC

Scope inspected:

- Admin and supervisor group matching, permission payloads, and trusted-proxy role mapping in `app.py`.
- Admin-only config, diagnostics, audit CSV, delete, sync, and approval paths.
- Supervisor template and employee edit permissions.
- Non-admin change-request handoff behavior.

Tests added/updated:

- No new test was needed; existing focused HTTP tests already cover admin, supervisor, and non-admin boundaries.

Commands:

- `python -m unittest tests.test_app.HttpTests.test_http_access_templates_and_supervisor_modify_without_admin_controls tests.test_app.HttpTests.test_non_admin_update_creates_change_request_for_admin_approval tests.test_app.HttpTests.test_trusted_proxy_auth_uses_ad_group_headers_for_admin_actions tests.test_app.HttpTests.test_admin_config_requires_domain_admin_and_masks_secrets tests.test_app.HttpTests.test_admin_diagnostics_requires_domain_admin_and_redacts_secrets`: passed, 5 tests.
- `python scripts\verify.py`: passed, 56 backend/UI tests with 2 Windows-local skips and 14 frontend monitor tests.

## Remaining Sections

Not yet completed in this branch:

15. Accessibility
16. Responsive/cross-browser behavior
17. Motion/effects/reduced-motion behavior
18. Performance and bundle health
20. Analytics/observability, if present
22. Final e2e regression
