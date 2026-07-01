#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_HOSTNAME="gatewatch.example.com"
DEFAULT_CONTAINER_NAME="gatewatch"
DEFAULT_VOLUME_NAME="gatewatch-data"
DEFAULT_IMAGE_NAME="gatewatch:latest"
DEFAULT_APP_PORT="8087"
DEFAULT_OAUTH2_PORT="4180"
DEFAULT_OAUTH2_PROXY_VERSION="v7.15.3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOSTNAME_VALUE="${GATEWATCH_HOSTNAME:-${DEFAULT_HOSTNAME}}"
TENANT_ID="${GATEWATCH_ENTRA_TENANT_ID:-}"
CLIENT_ID="${GATEWATCH_ENTRA_CLIENT_ID:-}"
CLIENT_SECRET="${GATEWATCH_ENTRA_CLIENT_SECRET:-}"
ADMIN_GROUP="${GATEWATCH_ADMIN_GROUP:-${GATEWATCH_ADMIN_GROUP_CANONICAL:-}}"
SUPERVISOR_GROUP="${GATEWATCH_SUPERVISOR_GROUP:-${GATEWATCH_SUPERVISOR_GROUP_CANONICAL:-}}"
CONTAINER_NAME="${GATEWATCH_CONTAINER_NAME:-${DEFAULT_CONTAINER_NAME}}"
VOLUME_NAME="${GATEWATCH_VOLUME_NAME:-${DEFAULT_VOLUME_NAME}}"
IMAGE_NAME="${GATEWATCH_IMAGE_NAME:-${DEFAULT_IMAGE_NAME}}"
APP_PORT="${GATEWATCH_PORT:-${DEFAULT_APP_PORT}}"
OAUTH2_PORT="${GATEWATCH_OAUTH2_PORT:-${DEFAULT_OAUTH2_PORT}}"
OAUTH2_PROXY_VERSION="${OAUTH2_PROXY_VERSION:-${DEFAULT_OAUTH2_PROXY_VERSION}}"
CERT_FILE="${GATEWATCH_TLS_CERT_FILE:-}"
KEY_FILE="${GATEWATCH_TLS_KEY_FILE:-}"
SELF_SIGNED=0
AUTO_YES=0
VALIDATE_ONLY=0
SKIP_BUILD=0
ROTATE_APP_SECRETS=0

usage() {
  cat <<USAGE
Configure Gatewatch as a Docker app behind Nginx, OAuth2 Proxy, and Microsoft Entra.

Run from a Gatewatch checkout on the Ubuntu VM:
  sudo is not required, but the current user must be able to run sudo.

Usage:
  scripts/setup-docker-production.sh [options]

Required values can be passed as flags or entered interactively:
  --hostname HOST               Public DNS name. Default: ${DEFAULT_HOSTNAME}
  --tenant-id ID                Microsoft Entra tenant ID.
  --client-id ID                Entra app registration client ID.
  --client-secret SECRET        Entra client secret VALUE. Prefer GATEWATCH_ENTRA_CLIENT_SECRET or the hidden prompt so secrets do not land in shell history.
  --admin-group ID_OR_NAME      Entra admin group object ID or emitted canonical name.
  --supervisor-group ID_OR_NAME Entra supervisor group object ID or emitted canonical name.

Options:
  --cert-file PATH              Existing TLS certificate for Nginx.
  --key-file PATH               Existing TLS private key for Nginx.
  --self-signed-cert            Generate a temporary self-signed certificate.
  --container-name NAME         Docker container name. Default: ${DEFAULT_CONTAINER_NAME}
  --volume-name NAME            Docker volume for SQLite data. Default: ${DEFAULT_VOLUME_NAME}
  --image-name NAME             Docker image tag. Default: ${DEFAULT_IMAGE_NAME}
  --app-port PORT               Loopback Gatewatch port. Default: ${DEFAULT_APP_PORT}
  --oauth2-port PORT            Loopback OAuth2 Proxy port. Default: ${DEFAULT_OAUTH2_PORT}
  --oauth2-version VERSION      OAuth2 Proxy release. Default: ${DEFAULT_OAUTH2_PROXY_VERSION}
  --skip-build                  Reuse the existing Docker image tag.
  --rotate-app-secrets          Generate new Gatewatch proxy/session secrets instead of reusing them.
  --yes                         Non-interactive mode. Missing required values fail fast.
  --validate-only               Validate inputs and exit before privileged or network actions.
  -h, --help                    Show this help.

Entra prerequisites:
  - Redirect URI: https://HOST/oauth2/callback
  - Microsoft Graph Application permission: User.Read.All
  - Admin consent granted
  - Group claims should ideally emit only groups assigned to the app.
USAGE
}

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  [[ -n "${value}" ]] || die "${flag} requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      require_value "$1" "${2:-}"
      HOSTNAME_VALUE="$2"
      shift 2
      ;;
    --tenant-id)
      require_value "$1" "${2:-}"
      TENANT_ID="$2"
      shift 2
      ;;
    --client-id)
      require_value "$1" "${2:-}"
      CLIENT_ID="$2"
      shift 2
      ;;
    --client-secret)
      require_value "$1" "${2:-}"
      CLIENT_SECRET="$2"
      shift 2
      ;;
    --admin-group)
      require_value "$1" "${2:-}"
      ADMIN_GROUP="$2"
      shift 2
      ;;
    --supervisor-group)
      require_value "$1" "${2:-}"
      SUPERVISOR_GROUP="$2"
      shift 2
      ;;
    --cert-file)
      require_value "$1" "${2:-}"
      CERT_FILE="$2"
      shift 2
      ;;
    --key-file)
      require_value "$1" "${2:-}"
      KEY_FILE="$2"
      shift 2
      ;;
    --self-signed-cert)
      SELF_SIGNED=1
      shift
      ;;
    --container-name)
      require_value "$1" "${2:-}"
      CONTAINER_NAME="$2"
      shift 2
      ;;
    --volume-name)
      require_value "$1" "${2:-}"
      VOLUME_NAME="$2"
      shift 2
      ;;
    --image-name)
      require_value "$1" "${2:-}"
      IMAGE_NAME="$2"
      shift 2
      ;;
    --app-port)
      require_value "$1" "${2:-}"
      APP_PORT="$2"
      shift 2
      ;;
    --oauth2-port)
      require_value "$1" "${2:-}"
      OAUTH2_PORT="$2"
      shift 2
      ;;
    --oauth2-version)
      require_value "$1" "${2:-}"
      OAUTH2_PROXY_VERSION="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --rotate-app-secrets)
      ROTATE_APP_SECRETS=1
      shift
      ;;
    --yes|--non-interactive)
      AUTO_YES=1
      shift
      ;;
    --validate-only)
      VALIDATE_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

if [[ ${EUID} -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

prompt_value() {
  local var_name="$1"
  local label="$2"
  local current="${!var_name:-}"
  if [[ -n "${current}" ]]; then
    return
  fi
  if [[ "${AUTO_YES}" == "1" ]]; then
    die "${label} is required in --yes mode"
  fi
  read -r -p "${label}: " current
  [[ -n "${current}" ]] || die "${label} is required"
  printf -v "${var_name}" "%s" "${current}"
}

prompt_secret() {
  local var_name="$1"
  local label="$2"
  local current="${!var_name:-}"
  if [[ -n "${current}" ]]; then
    return
  fi
  if [[ "${AUTO_YES}" == "1" ]]; then
    die "${label} is required in --yes mode"
  fi
  read -r -s -p "${label}: " current
  echo
  [[ -n "${current}" ]] || die "${label} is required"
  printf -v "${var_name}" "%s" "${current}"
}

valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

valid_hostname() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]$ ]] \
    && [[ "$1" != *..* ]] \
    && [[ "$1" != *.-* ]] \
    && [[ "$1" != *-.* ]]
}

valid_docker_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]
}

valid_image_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,180}$ ]]
}

valid_oauth2_version() {
  [[ "$1" =~ ^v[0-9]+(\.[0-9]+){1,3}$ ]]
}

validate_inputs() {
  prompt_value HOSTNAME_VALUE "Gatewatch public hostname"
  prompt_value TENANT_ID "Microsoft Entra tenant ID"
  prompt_value CLIENT_ID "Entra app registration client ID"
  prompt_secret CLIENT_SECRET "Entra client secret VALUE"
  prompt_value ADMIN_GROUP "Gatewatch admin group object ID or canonical name"
  prompt_value SUPERVISOR_GROUP "Gatewatch supervisor group object ID or canonical name"

  [[ -n "${HOSTNAME_VALUE}" ]] || die "--hostname is required"
  [[ -n "${TENANT_ID}" ]] || die "--tenant-id is required"
  [[ -n "${CLIENT_ID}" ]] || die "--client-id is required"
  [[ -n "${CLIENT_SECRET}" ]] || die "--client-secret is required"
  [[ -n "${ADMIN_GROUP}" ]] || die "--admin-group is required"
  [[ -n "${SUPERVISOR_GROUP}" ]] || die "--supervisor-group is required"
  valid_hostname "${HOSTNAME_VALUE}" || die "--hostname must contain only letters, numbers, dots, and hyphens"
  valid_docker_name "${CONTAINER_NAME}" || die "--container-name must use only letters, numbers, dots, underscores, and hyphens"
  valid_docker_name "${VOLUME_NAME}" || die "--volume-name must use only letters, numbers, dots, underscores, and hyphens"
  valid_image_name "${IMAGE_NAME}" || die "--image-name must be a simple Docker image reference without whitespace"
  valid_oauth2_version "${OAUTH2_PROXY_VERSION}" || die "--oauth2-version must look like v7.15.3"
  valid_port "${APP_PORT}" || die "--app-port must be a number from 1 to 65535"
  valid_port "${OAUTH2_PORT}" || die "--oauth2-port must be a number from 1 to 65535"
  [[ "${APP_PORT}" != "${OAUTH2_PORT}" ]] || die "--app-port and --oauth2-port must differ"

  if [[ -n "${CERT_FILE}" || -n "${KEY_FILE}" ]]; then
    [[ -n "${CERT_FILE}" && -n "${KEY_FILE}" ]] || die "--cert-file and --key-file must be provided together"
    [[ -f "${CERT_FILE}" ]] || die "TLS certificate not found: ${CERT_FILE}"
    [[ -f "${KEY_FILE}" ]] || die "TLS key not found: ${KEY_FILE}"
  elif [[ "${SELF_SIGNED}" != "1" ]]; then
    if [[ "${AUTO_YES}" == "1" ]]; then
      SELF_SIGNED=1
    else
      read -r -p "No TLS certificate provided. Generate a temporary self-signed certificate? [y/N] " answer
      case "${answer}" in
        y|Y|yes|YES) SELF_SIGNED=1 ;;
        *) die "provide --cert-file/--key-file or use --self-signed-cert" ;;
      esac
    fi
  fi
}

need() {
  if command -v "$1" >/dev/null 2>&1; then
    return
  fi
  if [[ -x "/usr/local/bin/$1" || -x "/usr/bin/$1" || -x "/usr/sbin/$1" ]]; then
    return
  fi
  die "$1 is required"
}

ensure_apt_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    die "apt-get is required on this Ubuntu deployment path"
  fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl nginx openssl tar python3
}

install_oauth2_proxy() {
  if command -v oauth2-proxy >/dev/null 2>&1; then
    info "OAuth2 Proxy already installed: $(oauth2-proxy --version 2>&1 | head -n 1)"
    return
  fi

  local machine arch archive temp_dir
  machine="$(uname -m)"
  case "${machine}" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) die "unsupported CPU architecture for OAuth2 Proxy: ${machine}" ;;
  esac

  archive="oauth2-proxy-${OAUTH2_PROXY_VERSION}.linux-${arch}.tar.gz"
  temp_dir="$(mktemp -d -t gatewatch-oauth2.XXXXXX)"
  trap 'rm -rf "${temp_dir:-}"' EXIT

  info "Downloading OAuth2 Proxy ${OAUTH2_PROXY_VERSION}"
  curl -fL "https://github.com/oauth2-proxy/oauth2-proxy/releases/download/${OAUTH2_PROXY_VERSION}/${archive}" \
    -o "${temp_dir}/${archive}"
  tar -xzf "${temp_dir}/${archive}" -C "${temp_dir}"
  "${SUDO[@]}" install -m 0755 "${temp_dir}/oauth2-proxy-${OAUTH2_PROXY_VERSION}.linux-${arch}/oauth2-proxy" /usr/local/bin/oauth2-proxy
  info "Installed $(/usr/local/bin/oauth2-proxy --version 2>&1 | head -n 1)"
}

docker_env_value() {
  local key="$1"
  if ! "${SUDO[@]}" docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    return 0
  fi
  "${SUDO[@]}" docker inspect "${CONTAINER_NAME}" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n "s/^${key}=//p" \
    | tail -n 1
}

random_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local insecure="${3:-}"
  local curl_args=(-fsS)
  if [[ "${insecure}" == "insecure" ]]; then
    curl_args=(-kfsS)
  fi
  for _ in $(seq 1 40); do
    if curl "${curl_args[@]}" "${url}" >/dev/null 2>&1; then
      info "${label} is healthy"
      return
    fi
    sleep 1
  done
  die "${label} did not become healthy at ${url}"
}

configure_gatewatch_container() {
  local proxy_secret session_secret
  proxy_secret="$(docker_env_value GATEWATCH_PROXY_SECRET || true)"
  session_secret="$(docker_env_value GATEWATCH_SESSION_SECRET || true)"

  if [[ "${ROTATE_APP_SECRETS}" == "1" || ${#proxy_secret} -lt 16 ]]; then
    proxy_secret="$(random_secret)"
  fi
  if [[ "${ROTATE_APP_SECRETS}" == "1" || -z "${session_secret}" ]]; then
    session_secret="$(random_secret)"
  fi

  export GATEWATCH_PROXY_SECRET="${proxy_secret}"
  export GATEWATCH_SESSION_SECRET="${session_secret}"

  if [[ "${SKIP_BUILD}" != "1" ]]; then
    info "Building ${IMAGE_NAME}"
    "${SUDO[@]}" docker build -t "${IMAGE_NAME}" "${REPO_ROOT}"
  else
    info "Skipping Docker build; reusing ${IMAGE_NAME}"
  fi

  "${SUDO[@]}" docker volume create "${VOLUME_NAME}" >/dev/null
  "${SUDO[@]}" docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  info "Starting ${CONTAINER_NAME} on 127.0.0.1:${APP_PORT}"
  "${SUDO[@]}" docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    -p "127.0.0.1:${APP_PORT}:8087" \
    -v "${VOLUME_NAME}:/data" \
    -e GATEWATCH_HOST=0.0.0.0 \
    -e GATEWATCH_PORT=8087 \
    -e GATEWATCH_DB=/data/gatewatch.db \
    -e GATEWATCH_CONFIG_FILE=/data/gatewatch.env \
    -e GATEWATCH_ALLOW_INSECURE_NETWORK=1 \
    -e GATEWATCH_AUTH_MODE=trusted_proxy \
    -e GATEWATCH_UPDATE_MODE=volume \
    -e GATEWATCH_UPDATE_BRANCH=main \
    -e GATEWATCH_UPDATE_SOURCE_URL=https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz \
    -e GATEWATCH_UPDATE_DATA_DIR=/data \
    -e GATEWATCH_UPDATE_STATUS_FILE=/data/gatewatch-update-status.json \
    -e GATEWATCH_UPDATE_LOG_FILE=/data/gatewatch-update.log \
    -e "GATEWATCH_PROXY_SECRET=${GATEWATCH_PROXY_SECRET}" \
    -e "GATEWATCH_SESSION_SECRET=${GATEWATCH_SESSION_SECRET}" \
    -e "GATEWATCH_ENTRA_TENANT_ID=${TENANT_ID}" \
    -e "GATEWATCH_ENTRA_CLIENT_ID=${CLIENT_ID}" \
    -e "GATEWATCH_ENTRA_CLIENT_SECRET=${CLIENT_SECRET}" \
    -e "GATEWATCH_ADMIN_GROUP_CANONICAL=${ADMIN_GROUP}" \
    -e "GATEWATCH_SUPERVISOR_GROUP_CANONICAL=${SUPERVISOR_GROUP}" \
    "${IMAGE_NAME}" >/dev/null

  wait_for_http "http://127.0.0.1:${APP_PORT}/healthz" "Gatewatch"

  info "Persisting Gatewatch runtime config in Docker volume ${VOLUME_NAME}"
  "${SUDO[@]}" docker exec -i \
    -e "GATEWATCH_PROXY_SECRET=${GATEWATCH_PROXY_SECRET}" \
    -e "GATEWATCH_SESSION_SECRET=${GATEWATCH_SESSION_SECRET}" \
    -e "GATEWATCH_ENTRA_CLIENT_SECRET=${CLIENT_SECRET}" \
    -e "GATEWATCH_ADMIN_GROUP=${ADMIN_GROUP}" \
    -e "GATEWATCH_SUPERVISOR_GROUP=${SUPERVISOR_GROUP}" \
    "${CONTAINER_NAME}" python - <<PY
from pathlib import Path
import os

values = {
    "GATEWATCH_HOST": "0.0.0.0",
    "GATEWATCH_PORT": "8087",
    "GATEWATCH_DB": "/data/gatewatch.db",
    "GATEWATCH_CONFIG_FILE": "/data/gatewatch.env",
    "GATEWATCH_ALLOW_INSECURE_NETWORK": "1",
    "GATEWATCH_AUTH_MODE": "trusted_proxy",
    "GATEWATCH_UPDATE_MODE": "volume",
    "GATEWATCH_UPDATE_BRANCH": "main",
    "GATEWATCH_UPDATE_SOURCE_URL": "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz",
    "GATEWATCH_UPDATE_DATA_DIR": "/data",
    "GATEWATCH_UPDATE_STATUS_FILE": "/data/gatewatch-update-status.json",
    "GATEWATCH_UPDATE_LOG_FILE": "/data/gatewatch-update.log",
    "GATEWATCH_PROXY_SECRET": os.environ["GATEWATCH_PROXY_SECRET"],
    "GATEWATCH_SESSION_SECRET": os.environ["GATEWATCH_SESSION_SECRET"],
    "GATEWATCH_ENTRA_TENANT_ID": "${TENANT_ID}",
    "GATEWATCH_ENTRA_CLIENT_ID": "${CLIENT_ID}",
    "GATEWATCH_ENTRA_CLIENT_SECRET": os.environ["GATEWATCH_ENTRA_CLIENT_SECRET"],
    "GATEWATCH_ADMIN_GROUP_CANONICAL": os.environ["GATEWATCH_ADMIN_GROUP"],
    "GATEWATCH_SUPERVISOR_GROUP_CANONICAL": os.environ["GATEWATCH_SUPERVISOR_GROUP"],
}

def quote(value: str) -> str:
    return '"' + value.replace("\\\\", "\\\\\\\\").replace('"', '\\\\"') + '"'

path = Path("/data/gatewatch.env")
path.write_text("\\n".join(f"{key}={quote(value)}" for key, value in values.items()) + "\\n", encoding="utf-8")
path.chmod(0o600)
PY

  "${SUDO[@]}" docker restart "${CONTAINER_NAME}" >/dev/null
  wait_for_http "http://127.0.0.1:${APP_PORT}/healthz" "Gatewatch after config persist"
}

install_tls_certificate() {
  "${SUDO[@]}" install -d -m 0755 /etc/ssl/gatewatch
  if [[ -n "${CERT_FILE}" && -n "${KEY_FILE}" ]]; then
    info "Installing provided TLS certificate"
    "${SUDO[@]}" install -m 0644 "${CERT_FILE}" /etc/ssl/gatewatch/gatewatch.crt
    "${SUDO[@]}" install -m 0600 "${KEY_FILE}" /etc/ssl/gatewatch/gatewatch.key
  else
    info "Generating temporary self-signed TLS certificate"
    "${SUDO[@]}" openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
      -keyout /etc/ssl/gatewatch/gatewatch.key \
      -out /etc/ssl/gatewatch/gatewatch.crt \
      -subj "/CN=${HOSTNAME_VALUE}" \
      -addext "subjectAltName=DNS:${HOSTNAME_VALUE}"
    "${SUDO[@]}" chmod 0600 /etc/ssl/gatewatch/gatewatch.key
  fi
}

configure_oauth2_proxy() {
  local cookie_secret
  cookie_secret="$(python3 -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

  "${SUDO[@]}" useradd --system --home-dir /var/lib/oauth2-proxy --shell /usr/sbin/nologin oauth2-proxy 2>/dev/null || true
  "${SUDO[@]}" install -d -m 0750 -o root -g oauth2-proxy /etc/oauth2-proxy

  info "Writing /etc/oauth2-proxy/gatewatch.env"
  umask 077
  "${SUDO[@]}" tee /etc/oauth2-proxy/gatewatch.env >/dev/null <<EOF
OAUTH2_PROXY_PROVIDER=entra-id
OAUTH2_PROXY_PROVIDER_DISPLAY_NAME=Microsoft Entra ID
OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/${TENANT_ID}/v2.0
OAUTH2_PROXY_CLIENT_ID=${CLIENT_ID}
OAUTH2_PROXY_CLIENT_SECRET=${CLIENT_SECRET}
OAUTH2_PROXY_REDIRECT_URL=https://${HOSTNAME_VALUE}/oauth2/callback
OAUTH2_PROXY_COOKIE_SECRET=${cookie_secret}
OAUTH2_PROXY_COOKIE_SECURE=true
OAUTH2_PROXY_COOKIE_HTTPONLY=true
OAUTH2_PROXY_COOKIE_SAMESITE=lax
OAUTH2_PROXY_COOKIE_EXPIRE=8h
OAUTH2_PROXY_HTTP_ADDRESS=127.0.0.1:${OAUTH2_PORT}
OAUTH2_PROXY_REVERSE_PROXY=true
OAUTH2_PROXY_TRUSTED_PROXY_IPS=127.0.0.1/32,::1/128
OAUTH2_PROXY_EMAIL_DOMAINS=*
OAUTH2_PROXY_SCOPE=openid email profile
OAUTH2_PROXY_OIDC_GROUPS_CLAIM=groups
OAUTH2_PROXY_ALLOWED_GROUPS=${ADMIN_GROUP},${SUPERVISOR_GROUP}
OAUTH2_PROXY_WHITELIST_DOMAINS=${HOSTNAME_VALUE}
OAUTH2_PROXY_SET_XAUTHREQUEST=true
OAUTH2_PROXY_PASS_BASIC_AUTH=false
OAUTH2_PROXY_PASS_USER_HEADERS=false
OAUTH2_PROXY_SKIP_PROVIDER_BUTTON=true
EOF
  "${SUDO[@]}" chown root:oauth2-proxy /etc/oauth2-proxy/gatewatch.env
  "${SUDO[@]}" chmod 0640 /etc/oauth2-proxy/gatewatch.env

  if [[ -f "${REPO_ROOT}/deploy/reverse-proxy/oauth2-proxy-gatewatch.service" ]]; then
    "${SUDO[@]}" install -m 0644 "${REPO_ROOT}/deploy/reverse-proxy/oauth2-proxy-gatewatch.service" /etc/systemd/system/oauth2-proxy-gatewatch.service
  else
    "${SUDO[@]}" tee /etc/systemd/system/oauth2-proxy-gatewatch.service >/dev/null <<'EOF'
[Unit]
Description=OAuth2 Proxy for Gatewatch
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=oauth2-proxy
Group=oauth2-proxy
EnvironmentFile=/etc/oauth2-proxy/gatewatch.env
ExecStart=/usr/local/bin/oauth2-proxy
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
StateDirectory=oauth2-proxy
RuntimeDirectory=oauth2-proxy
RuntimeDirectoryMode=0750

[Install]
WantedBy=multi-user.target
EOF
  fi

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable --now oauth2-proxy-gatewatch.service
  "${SUDO[@]}" systemctl restart oauth2-proxy-gatewatch.service
  wait_for_http "http://127.0.0.1:${OAUTH2_PORT}/ping" "OAuth2 Proxy"
}

configure_nginx() {
  "${SUDO[@]}" install -d -m 0755 /etc/nginx/snippets
  "${SUDO[@]}" tee /etc/nginx/snippets/gatewatch-proxy-secret.conf >/dev/null <<EOF
set \$gatewatch_proxy_secret "${GATEWATCH_PROXY_SECRET}";
EOF
  "${SUDO[@]}" chown root:www-data /etc/nginx/snippets/gatewatch-proxy-secret.conf 2>/dev/null || true
  "${SUDO[@]}" chmod 0640 /etc/nginx/snippets/gatewatch-proxy-secret.conf

  info "Writing /etc/nginx/sites-available/gatewatch"
  "${SUDO[@]}" tee /etc/nginx/sites-available/gatewatch >/dev/null <<EOF
upstream gatewatch_app {
    server 127.0.0.1:${APP_PORT};
    keepalive 16;
}

upstream gatewatch_oauth2_proxy {
    server 127.0.0.1:${OAUTH2_PORT};
    keepalive 8;
}

server {
    listen 80;
    server_name ${HOSTNAME_VALUE};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name ${HOSTNAME_VALUE};

    ssl_certificate /etc/ssl/gatewatch/gatewatch.crt;
    ssl_certificate_key /etc/ssl/gatewatch/gatewatch.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    include /etc/nginx/snippets/gatewatch-proxy-secret.conf;

    client_max_body_size 2m;
    large_client_header_buffers 8 64k;
    proxy_buffer_size 128k;
    proxy_buffers 16 128k;
    proxy_busy_buffers_size 256k;

    location /oauth2/ {
        proxy_pass http://gatewatch_oauth2_proxy;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Auth-Request-Redirect \$scheme://\$host\$request_uri;
    }

    location = /oauth2/auth {
        internal;
        proxy_pass http://gatewatch_oauth2_proxy;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Uri \$request_uri;
        proxy_set_header X-Original-URI \$request_uri;
    }

    location / {
        auth_request /oauth2/auth;
        error_page 401 = @oauth2_signin;

        auth_request_set \$auth_user \$upstream_http_x_auth_request_user;
        auth_request_set \$auth_email \$upstream_http_x_auth_request_email;
        auth_request_set \$auth_groups \$upstream_http_x_auth_request_groups;

        proxy_pass http://gatewatch_app;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        proxy_set_header X-Forwarded-User "";
        proxy_set_header X-Forwarded-Name "";
        proxy_set_header X-Forwarded-Email "";
        proxy_set_header X-Forwarded-Groups "";
        proxy_set_header X-Forwarded-Tenant "";
        proxy_set_header X-Authenticated-User "";
        proxy_set_header X-Authenticated-Groups "";
        proxy_set_header X-Access-Register-Proxy-Secret "";

        proxy_set_header X-Gatewatch-Proxy-Secret \$gatewatch_proxy_secret;
        proxy_set_header X-Remote-User \$auth_user;
        proxy_set_header X-Remote-Name \$auth_user;
        proxy_set_header X-Remote-Email \$auth_email;
        proxy_set_header X-Remote-Groups \$auth_groups;
        proxy_set_header X-Remote-Tenant "${TENANT_ID}";
    }

    location @oauth2_signin {
        return 302 /oauth2/start?rd=\$scheme://\$host\$request_uri;
    }
}
EOF

  "${SUDO[@]}" ln -sf /etc/nginx/sites-available/gatewatch /etc/nginx/sites-enabled/gatewatch
  "${SUDO[@]}" rm -f /etc/nginx/sites-enabled/default
  "${SUDO[@]}" nginx -t
  "${SUDO[@]}" systemctl enable --now nginx
  "${SUDO[@]}" systemctl reload nginx
}

verify_deployment() {
  local auth_status
  info "Verifying local health endpoints"
  curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null
  curl -fsSI "http://127.0.0.1:${OAUTH2_PORT}/ping" >/dev/null
  curl -kfsSI "https://${HOSTNAME_VALUE}/oauth2/start" >/dev/null

  info "Verifying Gatewatch trusted-proxy admin and Graph configuration"
  auth_status="$(
    curl -fsS \
      -H "X-Gatewatch-Proxy-Secret: ${GATEWATCH_PROXY_SECRET}" \
      -H "X-Remote-User: deploy.verify@${HOSTNAME_VALUE}" \
      -H "X-Remote-Email: deploy.verify@${HOSTNAME_VALUE}" \
      -H "X-Remote-Groups: ${ADMIN_GROUP}" \
      -H "X-Remote-Tenant: ${TENANT_ID}" \
      "http://127.0.0.1:${APP_PORT}/api/auth/status"
  )"
  AUTH_STATUS="${auth_status}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["AUTH_STATUS"])
entra = payload["entra"]
perms = entra["permissions"]
if not entra["graphConfigured"]:
    raise SystemExit("Gatewatch reports graphConfigured=false")
if not perms["canAdministerSystem"]:
    raise SystemExit("Gatewatch did not grant admin permissions to the configured admin group")
print("Gatewatch auth check passed: graphConfigured=true, role=admin")
PY

  echo
  info "Production setup complete"
  echo "Open: https://${HOSTNAME_VALUE}"
  if [[ "${SELF_SIGNED}" == "1" ]]; then
    echo "Note: a temporary self-signed certificate is installed. Replace it with a company-trusted certificate before broad rollout."
  fi
  echo "Entra reminder: grant Microsoft Graph User.Read.All as an Application permission with admin consent for directory sync."
}

validate_inputs
if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  echo "Production setup validation passed"
  exit 0
fi

cd "${REPO_ROOT}"
ensure_apt_packages
need docker
need python3
need curl
need openssl
need nginx
install_oauth2_proxy
configure_gatewatch_container
install_tls_certificate
configure_oauth2_proxy
configure_nginx
verify_deployment
