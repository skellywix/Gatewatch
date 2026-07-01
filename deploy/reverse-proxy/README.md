# Gatewatch Production Reverse Proxy Bundle

This bundle is for an Ubuntu VM where Gatewatch stays on loopback and Nginx is the only public listener.

The production shape is:

1. Gatewatch listens on `127.0.0.1:8087` with `GATEWATCH_AUTH_MODE=trusted_proxy`.
2. OAuth2 Proxy signs users in with Microsoft Entra ID and returns `X-Auth-Request-*` headers to Nginx.
3. Nginx strips any client-supplied identity headers, injects Gatewatch's `X-Remote-*` headers, adds `X-Gatewatch-Proxy-Secret`, and proxies to Gatewatch.

Use this when you want browser access to go through an Entra-authenticated reverse proxy. If you want Gatewatch's native Entra login instead, keep `GATEWATCH_AUTH_MODE=local`, set `GATEWATCH_ENTRA_REDIRECT_URI=https://YOUR_HOST/auth/entra/callback`, and use a plain TLS reverse proxy.

## Files

- `nginx-gatewatch.conf`: Nginx site config for TLS, OAuth2 Proxy `auth_request`, header stripping, and trusted-proxy header injection.
- `nginx-gatewatch-proxy-secret.conf.example`: local-only Nginx snippet that holds the shared proxy secret.
- `oauth2-proxy-gatewatch.env.example`: OAuth2 Proxy environment file for Microsoft Entra ID.
- `oauth2-proxy-gatewatch.service`: systemd unit for OAuth2 Proxy.

## Entra App Registration

Create or reuse a single-tenant Microsoft Entra app registration for `https://gatewatch.example.com`.

Add this Web redirect URI for OAuth2 Proxy:

```text
https://gatewatch.example.com/oauth2/callback
```

For group-based Gatewatch permissions, make the groups claim available to OAuth2 Proxy. The least-noisy setup is to assign only the Gatewatch admin and supervisor groups to the app, then configure group claims for groups assigned to the application. That also avoids large-token group overage behavior in tenants where users belong to many groups. If Entra emits group object IDs, set Gatewatch's `GATEWATCH_ADMIN_GROUP_CANONICAL` and `GATEWATCH_SUPERVISOR_GROUP_CANONICAL` to those object IDs. If your tenant emits display names or synced AD names, the existing canonical names can remain names such as `gcefcu.org/Users/Domain Admins`.

If you want Gatewatch directory sync, the same app registration also needs Microsoft Graph application permission such as `User.Read.All` and admin consent. Gatewatch uses the tenant ID, client ID, and client secret for Graph sync even though browser sign-in is handled by OAuth2 Proxy.

## Install Gatewatch

Run the current Ubuntu installer with loopback binding and trusted-proxy mode:

```bash
export GATEWATCH_HOSTNAME="gatewatch.example.com"
export GATEWATCH_ENTRA_TENANT_ID="00000000-0000-0000-0000-000000000000"
export GATEWATCH_ENTRA_CLIENT_ID="00000000-0000-0000-0000-000000000000"
export GATEWATCH_ENTRA_CLIENT_SECRET="paste-client-secret-here"
export GATEWATCH_PROXY_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export GATEWATCH_ADMIN_GROUP="paste-admin-group-object-id-or-canonical-name"
export GATEWATCH_SUPERVISOR_GROUP="paste-supervisor-group-object-id-or-canonical-name"

curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash -s -- \
  --yes \
  --host 127.0.0.1 \
  --port 8087 \
  --auth-mode trusted_proxy \
  --proxy-secret "${GATEWATCH_PROXY_SECRET}" \
  --entra-tenant-id "${GATEWATCH_ENTRA_TENANT_ID}" \
  --entra-client-id "${GATEWATCH_ENTRA_CLIENT_ID}" \
  --entra-client-secret "${GATEWATCH_ENTRA_CLIENT_SECRET}" \
  --admin-group-canonical "${GATEWATCH_ADMIN_GROUP}" \
  --supervisor-group-canonical "${GATEWATCH_SUPERVISOR_GROUP}"
```

Do not use `--host 0.0.0.0` for this deployment. Nginx should be the public listener.

## Install OAuth2 Proxy

Install the OAuth2 Proxy binary using your normal package-management path, then install the environment file and unit:

```bash
sudo useradd --system --home-dir /var/lib/oauth2-proxy --shell /usr/sbin/nologin oauth2-proxy 2>/dev/null || true
sudo install -d -m 0750 -o root -g root /etc/oauth2-proxy
sudo install -m 0640 -o root -g oauth2-proxy deploy/reverse-proxy/oauth2-proxy-gatewatch.env.example /etc/oauth2-proxy/gatewatch.env
sudo editor /etc/oauth2-proxy/gatewatch.env
sudo install -m 0644 deploy/reverse-proxy/oauth2-proxy-gatewatch.service /etc/systemd/system/oauth2-proxy-gatewatch.service
sudo systemctl daemon-reload
sudo systemctl enable --now oauth2-proxy-gatewatch.service
```

Set these values in `/etc/oauth2-proxy/gatewatch.env`:

- `OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/TENANT_ID/v2.0`
- `OAUTH2_PROXY_CLIENT_ID`
- `OAUTH2_PROXY_CLIENT_SECRET`
- `OAUTH2_PROXY_COOKIE_SECRET`
- `OAUTH2_PROXY_REDIRECT_URL=https://gatewatch.example.com/oauth2/callback`

Generate the OAuth2 Proxy cookie secret with:

```bash
python3 -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

## Install Nginx

Install the site config and a local secret snippet. The snippet value must exactly match `GATEWATCH_PROXY_SECRET` in `/etc/gatewatch/gatewatch.env`.

```bash
sudo install -m 0644 deploy/reverse-proxy/nginx-gatewatch.conf /etc/nginx/sites-available/gatewatch
sudo sed -i "s/gatewatch.example.com/${GATEWATCH_HOSTNAME}/g" /etc/nginx/sites-available/gatewatch

sudo install -m 0640 -o root -g www-data deploy/reverse-proxy/nginx-gatewatch-proxy-secret.conf.example /etc/nginx/snippets/gatewatch-proxy-secret.conf
sudo sed -i "s/REPLACE_WITH_GATEWATCH_PROXY_SECRET/${GATEWATCH_PROXY_SECRET}/" /etc/nginx/snippets/gatewatch-proxy-secret.conf
sudo sed -i "s/REPLACE_WITH_ENTRA_TENANT_ID/${GATEWATCH_ENTRA_TENANT_ID}/" /etc/nginx/sites-available/gatewatch

sudo ln -sf /etc/nginx/sites-available/gatewatch /etc/nginx/sites-enabled/gatewatch
sudo nginx -t
sudo systemctl reload nginx
```

Provision the TLS certificate before reloading Nginx, or replace the certificate paths in `nginx-gatewatch.conf` with the paths already used on the VM.

## Verification Runbook

Run these checks on the Ubuntu VM.

```bash
systemctl status gatewatch.service --no-pager
systemctl status oauth2-proxy-gatewatch.service --no-pager
systemctl status nginx.service --no-pager
curl -fsS http://127.0.0.1:8087/healthz
curl -fsSI http://127.0.0.1:4180/ping
sudo nginx -t
```

Prove Gatewatch rejects direct identity spoofing without the shared secret:

```bash
curl -i \
  -H "X-Remote-User: attacker@example.com" \
  -H "X-Remote-Groups: ${GATEWATCH_ADMIN_GROUP}" \
  http://127.0.0.1:8087/api/auth/status
```

Expected result: `403` with a trusted proxy secret error.

Prove Gatewatch maps the configured admin group when the reverse proxy secret and headers are present:

```bash
curl -fsS \
  -H "X-Gatewatch-Proxy-Secret: ${GATEWATCH_PROXY_SECRET}" \
  -H "X-Remote-User: proxy.verify@example.com" \
  -H "X-Remote-Email: proxy.verify@example.com" \
  -H "X-Remote-Groups: ${GATEWATCH_ADMIN_GROUP}" \
  -H "X-Remote-Tenant: ${GATEWATCH_ENTRA_TENANT_ID}" \
  http://127.0.0.1:8087/api/auth/status | python3 -m json.tool
```

Expected result: `provider` is `trusted_proxy`, `canAdministerSystem` is `true`, and the actor is `proxy.verify@example.com`.

Prove browser traffic is forced through Entra:

```bash
curl -I "https://${GATEWATCH_HOSTNAME}/"
```

Expected result before sign-in: a redirect into `/oauth2/` or Microsoft Entra sign-in. After signing in in a browser, open Gatewatch and confirm the Configuration tab shows the signed-in user with the expected admin or supervisor role.

Check logs without dumping secrets:

```bash
journalctl -u gatewatch.service -n 80 --no-pager
journalctl -u oauth2-proxy-gatewatch.service -n 80 --no-pager
journalctl -u nginx.service -n 80 --no-pager
```

## Rollback

```bash
sudo rm -f /etc/nginx/sites-enabled/gatewatch
sudo systemctl reload nginx
sudo systemctl disable --now oauth2-proxy-gatewatch.service
sudo sed -i 's/^GATEWATCH_AUTH_MODE=.*/GATEWATCH_AUTH_MODE="local"/' /etc/gatewatch/gatewatch.env
sudo sed -i 's/^GATEWATCH_PROXY_SECRET=.*/GATEWATCH_PROXY_SECRET=""/' /etc/gatewatch/gatewatch.env
sudo systemctl restart gatewatch.service
curl -fsS http://127.0.0.1:8087/healthz
```

Keep `/etc/gatewatch/gatewatch.env`, `/etc/oauth2-proxy/gatewatch.env`, and `/etc/nginx/snippets/gatewatch-proxy-secret.conf` private. They contain secrets or values that can grant trusted-proxy access.
