# Gatewatch Full-Test Proxy Lab

This lab runs Gatewatch in `trusted_proxy` mode behind a tiny local authenticated reverse proxy. The app container stays private on the Docker network; the browser only reaches the proxy.

## Start

From the repository root:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml up -d --build app proxy
```

Open:

```text
http://127.0.0.1:18107
```

The proxy injects this test identity:

```text
Grace Admin <grace.admin@gatewatch.test>
GATEWATCH\Gatewatch-Admins
```

Gatewatch maps `GATEWATCH\Gatewatch-Admins` to the configured Domain Admin group, so the browser session can use admin-only actions such as direct edits, deletes, Configuration, and Logs.

## Browser SSO Smoke

Run the smoke from the repository root:

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml run --rm browser-smoke
```

The smoke requests `/` and `/api/bootstrap` through the proxy, confirms the browser session is `trusted_proxy`, verifies `canModifyEmployees` is true, creates an employee, deletes it, and checks that the audit actor is the proxied user.

## Reset

```powershell
docker compose --env-file docker/full-test/.env.example -f docker/full-test/compose.yaml down -v
```

The values in `.env.example` are local lab secrets only. Do not reuse them in production.
