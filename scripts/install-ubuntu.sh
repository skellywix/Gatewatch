#!/usr/bin/env bash
set -euo pipefail

DEFAULT_SOURCE_URL="https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"
SOURCE_URL="${GATEWATCH_SOURCE_URL:-${DEFAULT_SOURCE_URL}}"
INSTALL_DIR="/opt/gatewatch"
DATA_DIR="/var/lib/gatewatch"
ENV_DIR="/etc/gatewatch"
SERVICE_USER="gatewatch"
SERVICE_NAME="gatewatch"
HOST="127.0.0.1"
PORT="8087"
ALLOW_NETWORK="0"
START_SERVICE="1"
ASSUME_YES="0"
ENTRA_TENANT_ID="${GATEWATCH_ENTRA_TENANT_ID:-}"
ENTRA_CLIENT_ID="${GATEWATCH_ENTRA_CLIENT_ID:-}"
ENTRA_CLIENT_SECRET="${GATEWATCH_ENTRA_CLIENT_SECRET:-}"
ENTRA_REDIRECT_URI="${GATEWATCH_ENTRA_REDIRECT_URI:-}"
ADMIN_GROUP_CANONICAL="${GATEWATCH_ADMIN_GROUP_CANONICAL:-gcefcu.org/Users/Domain Admins}"
SESSION_SECRET="${GATEWATCH_SESSION_SECRET:-}"
ORIGINAL_ARGS=("$@")
TEMP_DIR=""

usage() {
  cat <<'USAGE'
Gatewatch Ubuntu LTS one-line installer

One-line install:
  curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash

Usage:
  sudo bash scripts/install-ubuntu.sh [options]

Options:
  --yes, --non-interactive
                        Accept defaults and skip terminal prompts.
  --source-url URL      Gatewatch source tarball. Default: GitHub main branch archive.
  --install-dir PATH    App code directory. Default: /opt/gatewatch
  --data-dir PATH       SQLite data directory. Default: /var/lib/gatewatch
  --env-dir PATH        Environment file directory. Default: /etc/gatewatch
  --service-name NAME   systemd service name without .service. Default: gatewatch
  --service-user USER   Dedicated Linux service user. Default: gatewatch
  --host ADDRESS        Bind address. Default: 127.0.0.1
  --port PORT           HTTP port. Default: 8087
  --allow-network       Permit non-loopback binding. Use only behind protected internal access.
  --entra-tenant-id ID  Microsoft Entra tenant ID for SSO and Graph sync.
  --entra-client-id ID  Microsoft Entra app registration client ID.
  --entra-client-secret SECRET
                        Microsoft Entra app registration client secret.
  --entra-redirect-uri URI
                        Entra redirect URI. Default when prompted: http://HOST:PORT/auth/entra/callback
  --admin-group-canonical GROUP
                        AD/Entra group allowed to approve, delete, sync, and configure.
                        Default: gcefcu.org/Users/Domain Admins
  --session-secret SECRET
                        Cookie signing secret. Generated automatically when Entra is configured.
  --no-start            Install files and service, but do not start it.
  -h, --help            Show this help.

When prompts are enabled, press Enter to accept the default shown in brackets.
USAGE
}

cleanup() {
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf "${TEMP_DIR}"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|--non-interactive)
      ASSUME_YES="1"
      shift
      ;;
    --source-url)
      SOURCE_URL="${2:?Missing value for --source-url}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:?Missing value for --install-dir}"
      shift 2
      ;;
    --data-dir)
      DATA_DIR="${2:?Missing value for --data-dir}"
      shift 2
      ;;
    --env-dir)
      ENV_DIR="${2:?Missing value for --env-dir}"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="${2:?Missing value for --service-name}"
      shift 2
      ;;
    --service-user)
      SERVICE_USER="${2:?Missing value for --service-user}"
      shift 2
      ;;
    --host)
      HOST="${2:?Missing value for --host}"
      shift 2
      ;;
    --port)
      PORT="${2:?Missing value for --port}"
      shift 2
      ;;
    --allow-network)
      ALLOW_NETWORK="1"
      shift
      ;;
    --entra-tenant-id)
      ENTRA_TENANT_ID="${2:?Missing value for --entra-tenant-id}"
      shift 2
      ;;
    --entra-client-id)
      ENTRA_CLIENT_ID="${2:?Missing value for --entra-client-id}"
      shift 2
      ;;
    --entra-client-secret)
      ENTRA_CLIENT_SECRET="${2:?Missing value for --entra-client-secret}"
      shift 2
      ;;
    --entra-redirect-uri)
      ENTRA_REDIRECT_URI="${2:?Missing value for --entra-redirect-uri}"
      shift 2
      ;;
    --admin-group-canonical)
      ADMIN_GROUP_CANONICAL="${2:?Missing value for --admin-group-canonical}"
      shift 2
      ;;
    --session-secret)
      SESSION_SECRET="${2:?Missing value for --session-secret}"
      shift 2
      ;;
    --no-start)
      START_SERVICE="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  if [[ -f "${BASH_SOURCE[0]:-}" ]] && command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "${BASH_SOURCE[0]}" "${ORIGINAL_ARGS[@]}"
  fi
  cat >&2 <<'ERROR'
Run this installer as root.

For the one-line install, use:
  curl -fsSL https://raw.githubusercontent.com/skellywix/Gatewatch/main/scripts/install-ubuntu.sh | sudo bash
ERROR
  exit 1
fi

can_prompt() {
  [[ "${ASSUME_YES}" != "1" && -r /dev/tty && -w /dev/tty ]]
}

prompt_value() {
  local label="$1"
  local current="$2"
  local reply=""
  if can_prompt; then
    printf "%s [%s]: " "${label}" "${current}" > /dev/tty
    IFS= read -r reply < /dev/tty || true
    if [[ -n "${reply}" ]]; then
      printf "%s" "${reply}"
      return
    fi
  fi
  printf "%s" "${current}"
}

prompt_yes_no() {
  local label="$1"
  local default="$2"
  local reply=""
  if ! can_prompt; then
    [[ "${default}" == "yes" ]]
    return
  fi
  while true; do
    if [[ "${default}" == "yes" ]]; then
      printf "%s [Y/n]: " "${label}" > /dev/tty
    else
      printf "%s [y/N]: " "${label}" > /dev/tty
    fi
    IFS= read -r reply < /dev/tty || true
    reply="${reply,,}"
    case "${reply}" in
      "")
        [[ "${default}" == "yes" ]]
        return
        ;;
      y|yes)
        return 0
        ;;
      n|no)
        return 1
        ;;
      *)
        echo "Please answer yes or no." > /dev/tty
        ;;
    esac
  done
}

prompt_secret() {
  local label="$1"
  local current="$2"
  local reply=""
  if can_prompt; then
    if [[ -n "${current}" ]]; then
      printf "%s [stored, press Enter to keep]: " "${label}" > /dev/tty
    else
      printf "%s: " "${label}" > /dev/tty
    fi
    IFS= read -r -s reply < /dev/tty || true
    printf "\n" > /dev/tty
    if [[ -n "${reply}" ]]; then
      printf "%s" "${reply}"
      return
    fi
  fi
  printf "%s" "${current}"
}

if can_prompt; then
  cat > /dev/tty <<'PROMPT'

Gatewatch installer
Press Enter to accept a default. Keep Host at 127.0.0.1 unless access is protected by a reverse proxy, VPN, or SSH tunnel.

PROMPT
  INSTALL_DIR="$(prompt_value "Install directory" "${INSTALL_DIR}")"
  DATA_DIR="$(prompt_value "SQLite data directory" "${DATA_DIR}")"
  ENV_DIR="$(prompt_value "Environment directory" "${ENV_DIR}")"
  SERVICE_NAME="$(prompt_value "systemd service name" "${SERVICE_NAME}")"
  SERVICE_USER="$(prompt_value "Linux service user" "${SERVICE_USER}")"
  HOST="$(prompt_value "HTTP bind address" "${HOST}")"
  PORT="$(prompt_value "HTTP port" "${PORT}")"
  if prompt_yes_no "Start or restart the service after install?" "yes"; then
    START_SERVICE="1"
  else
    START_SERVICE="0"
  fi
  if prompt_yes_no "Configure Microsoft Entra ID SSO and directory sync now?" "no"; then
    ENTRA_TENANT_ID="$(prompt_value "Entra tenant ID" "${ENTRA_TENANT_ID}")"
    ENTRA_CLIENT_ID="$(prompt_value "Entra client ID" "${ENTRA_CLIENT_ID}")"
    ENTRA_CLIENT_SECRET="$(prompt_secret "Entra client secret" "${ENTRA_CLIENT_SECRET}")"
    DEFAULT_ENTRA_REDIRECT_URI="http://${HOST}:${PORT}/auth/entra/callback"
    ENTRA_REDIRECT_URI="$(prompt_value "Entra redirect URI" "${ENTRA_REDIRECT_URI:-${DEFAULT_ENTRA_REDIRECT_URI}}")"
    ADMIN_GROUP_CANONICAL="$(prompt_value "Admin group canonical name" "${ADMIN_GROUP_CANONICAL}")"
  fi
fi

fail() {
  echo "Error: $*" >&2
  exit 1
}

require_absolute_path() {
  local label="$1"
  local path="$2"
  case "${path}" in
    /*) ;;
    *) fail "${label} must be an absolute path" ;;
  esac
}

require_absolute_path "Install directory" "${INSTALL_DIR}"
require_absolute_path "Data directory" "${DATA_DIR}"
require_absolute_path "Environment directory" "${ENV_DIR}"

if ! [[ "${SERVICE_NAME}" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
  fail "--service-name may only contain letters, numbers, underscore, dot, @, and hyphen"
fi

if ! [[ "${SERVICE_USER}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]; then
  fail "--service-user must be a valid Linux service user name"
fi

case "${SOURCE_URL}" in
  http://*|https://*|file://*) ;;
  *) fail "--source-url must start with http://, https://, or file://" ;;
esac

case "${HOST}" in
  127.*|localhost|::1) ;;
  *)
    if [[ "${ALLOW_NETWORK}" != "1" ]]; then
      if can_prompt; then
        cat > /dev/tty <<WARNING

Gatewatch does not include built-in enterprise authentication.
Binding to ${HOST} can expose employee data to your network.
WARNING
        if prompt_yes_no "Allow this non-loopback bind anyway?" "no"; then
          ALLOW_NETWORK="1"
        else
          fail "Refusing non-loopback host '${HOST}' without explicit approval"
        fi
      else
        fail "Refusing non-loopback host '${HOST}' without --allow-network"
      fi
    fi
    ;;
esac

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  fail "--port must be a number from 1 to 65535"
fi

if [[ -n "${ENTRA_REDIRECT_URI}" ]]; then
  case "${ENTRA_REDIRECT_URI}" in
    http://*|https://*) ;;
    *) fail "--entra-redirect-uri must start with http:// or https://" ;;
  esac
fi

if [[ -z "${ADMIN_GROUP_CANONICAL}" ]]; then
  fail "--admin-group-canonical cannot be empty"
fi

if [[ -n "${ENTRA_TENANT_ID}${ENTRA_CLIENT_ID}${ENTRA_CLIENT_SECRET}" ]]; then
  if [[ -z "${ENTRA_TENANT_ID}" || -z "${ENTRA_CLIENT_ID}" || -z "${ENTRA_CLIENT_SECRET}" ]]; then
    fail "Entra sync requires tenant ID, client ID, and client secret"
  fi
fi

SERVICE_UNIT="${SERVICE_NAME}.service"

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Warning: this installer is tuned for Ubuntu LTS. Detected ${PRETTY_NAME:-unknown Linux}." >&2
  fi
fi

if ! command -v apt-get >/dev/null 2>&1; then
  fail "apt-get is required. This installer targets Ubuntu LTS."
fi

ensure_apt_packages() {
  local missing=()
  local package
  for package in "$@"; do
    if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q "install ok installed"; then
      missing+=("${package}")
    fi
  done
  if ((${#missing[@]})); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
  fi
}

ensure_apt_packages ca-certificates tar curl python3

if ! command -v systemctl >/dev/null 2>&1; then
  fail "systemctl is required. Install on a systemd-based Ubuntu host."
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
PY

if [[ -n "${ENTRA_TENANT_ID}${ENTRA_CLIENT_ID}${ENTRA_CLIENT_SECRET}" && -z "${SESSION_SECRET}" ]]; then
  SESSION_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
fi

has_source_files() {
  local source_dir="$1"
  [[ -f "${source_dir}/app.py" \
    && -f "${source_dir}/README.md" \
    && -f "${source_dir}/web/index.html" \
    && -f "${source_dir}/web/app.js" \
    && -f "${source_dir}/web/styles.css" ]]
}

SOURCE_DIR=""
SOURCE_LABEL=""
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  CANDIDATE_SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
  if has_source_files "${CANDIDATE_SOURCE_DIR}"; then
    SOURCE_DIR="${CANDIDATE_SOURCE_DIR}"
    SOURCE_LABEL="local checkout: ${SOURCE_DIR}"
  fi
fi

if [[ -z "${SOURCE_DIR}" ]]; then
  TEMP_DIR="$(mktemp -d)"
  ARCHIVE_PATH="${TEMP_DIR}/gatewatch.tar.gz"
  echo "Downloading Gatewatch source from ${SOURCE_URL}"
  curl -fsSL "${SOURCE_URL}" -o "${ARCHIVE_PATH}"
  mkdir -p "${TEMP_DIR}/source"
  tar -xzf "${ARCHIVE_PATH}" -C "${TEMP_DIR}/source" --strip-components=1 --no-same-owner
  SOURCE_DIR="${TEMP_DIR}/source"
  SOURCE_LABEL="${SOURCE_URL}"
fi

if ! has_source_files "${SOURCE_DIR}"; then
  fail "Gatewatch source is incomplete at ${SOURCE_DIR}"
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -m 0755 "${INSTALL_DIR}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${DATA_DIR}"
install -d -m 0770 -o root -g "${SERVICE_USER}" "${ENV_DIR}"

install -m 0644 "${SOURCE_DIR}/app.py" "${INSTALL_DIR}/app.py"
install -m 0644 "${SOURCE_DIR}/README.md" "${INSTALL_DIR}/README.md"
rm -rf "${INSTALL_DIR}/web"
install -d -m 0755 "${INSTALL_DIR}/web"
install -m 0644 "${SOURCE_DIR}/web/index.html" "${INSTALL_DIR}/web/index.html"
install -m 0644 "${SOURCE_DIR}/web/app.js" "${INSTALL_DIR}/web/app.js"
install -m 0644 "${SOURCE_DIR}/web/styles.css" "${INSTALL_DIR}/web/styles.css"

ENV_FILE="${ENV_DIR}/gatewatch.env"
write_env_var() {
  local key="$1"
  local value="$2"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf "%s=\"%s\"\n" "${key}" "${value}" >> "${ENV_FILE}"
}

: > "${ENV_FILE}"
write_env_var "GATEWATCH_HOST" "${HOST}"
write_env_var "GATEWATCH_PORT" "${PORT}"
write_env_var "GATEWATCH_DB" "${DATA_DIR}/gatewatch.db"
write_env_var "GATEWATCH_CONFIG_FILE" "${ENV_FILE}"
write_env_var "GATEWATCH_ALLOW_INSECURE_NETWORK" "${ALLOW_NETWORK}"
write_env_var "GATEWATCH_ADMIN_GROUP_CANONICAL" "${ADMIN_GROUP_CANONICAL}"
if [[ -n "${SESSION_SECRET}" ]]; then
  write_env_var "GATEWATCH_SESSION_SECRET" "${SESSION_SECRET}"
fi
if [[ -n "${ENTRA_TENANT_ID}" ]]; then
  write_env_var "GATEWATCH_ENTRA_TENANT_ID" "${ENTRA_TENANT_ID}"
  write_env_var "GATEWATCH_ENTRA_CLIENT_ID" "${ENTRA_CLIENT_ID}"
  write_env_var "GATEWATCH_ENTRA_CLIENT_SECRET" "${ENTRA_CLIENT_SECRET}"
fi
if [[ -n "${ENTRA_REDIRECT_URI}" ]]; then
  write_env_var "GATEWATCH_ENTRA_REDIRECT_URI" "${ENTRA_REDIRECT_URI}"
fi
chown root:"${SERVICE_USER}" "${ENV_FILE}"
chmod 0660 "${ENV_FILE}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_UNIT}"
cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Gatewatch employee tracker
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/app.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=${DATA_DIR} ${ENV_DIR}

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_UNIT}" >/dev/null

if [[ "${START_SERVICE}" == "1" ]]; then
  systemctl restart "${SERVICE_UNIT}"
  python3 - <<PY
import time
import urllib.request

url = "http://127.0.0.1:${PORT}/healthz"
last_error = None
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                print(f"Health check passed: {url}")
                raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)
raise SystemExit(f"Gatewatch did not become healthy at {url}: {last_error}")
PY
fi

cat <<DONE

Gatewatch installed.

Service: ${SERVICE_UNIT}
App:     http://${HOST}:${PORT}
Data:    ${DATA_DIR}/gatewatch.db
Env:     ${ENV_FILE}
Source:  ${SOURCE_LABEL}

Useful commands:
  systemctl status ${SERVICE_UNIT}
  journalctl -u ${SERVICE_UNIT} -f
  systemctl restart ${SERVICE_UNIT}
DONE
