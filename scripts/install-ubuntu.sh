#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/gatewatch"
DATA_DIR="/var/lib/gatewatch"
ENV_DIR="/etc/gatewatch"
SERVICE_USER="gatewatch"
SERVICE_NAME="gatewatch"
SERVICE_UNIT="gatewatch.service"
HOST="127.0.0.1"
PORT="8087"
ALLOW_NETWORK="0"
START_SERVICE="1"
ORIGINAL_ARGS=("$@")

usage() {
  cat <<'USAGE'
Gatewatch Ubuntu LTS one-click installer

Usage:
  sudo bash scripts/install-ubuntu.sh [options]

Options:
  --install-dir PATH     App code directory. Default: /opt/gatewatch
  --data-dir PATH        SQLite data directory. Default: /var/lib/gatewatch
  --host ADDRESS         Bind address. Default: 127.0.0.1
  --port PORT            HTTP port. Default: 8087
  --allow-network        Permit non-loopback binding. Use only behind a protected internal proxy.
  --no-start             Install files and service, but do not start it.
  -h, --help             Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="${2:?Missing value for --install-dir}"
      shift 2
      ;;
    --data-dir)
      DATA_DIR="${2:?Missing value for --data-dir}"
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
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Run this installer as root or install sudo first." >&2
    exit 1
  fi
  exec sudo -E bash "$0" "${ORIGINAL_ARGS[@]}"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Warning: this installer is tuned for Ubuntu LTS. Detected ${PRETTY_NAME:-unknown Linux}." >&2
  fi
fi

case "${HOST}" in
  127.*|localhost|::1) ;;
  *)
    if [[ "${ALLOW_NETWORK}" != "1" ]]; then
      echo "Refusing non-loopback host '${HOST}' without --allow-network." >&2
      exit 1
    fi
    ;;
esac

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "--port must be a number from 1 to 65535." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

require_source_file() {
  if [[ ! -f "${SOURCE_DIR}/$1" ]]; then
    echo "Missing ${SOURCE_DIR}/$1. Run this from a complete Gatewatch checkout." >&2
    exit 1
  fi
}

require_source_file "app.py"
require_source_file "web/index.html"
require_source_file "web/app.js"
require_source_file "web/styles.css"

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    if python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      return
    fi
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required and apt-get is not available." >&2
    exit 1
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3
  python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
PY
}

ensure_python

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -m 0755 "${INSTALL_DIR}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${DATA_DIR}"
install -d -m 0755 "${ENV_DIR}"

install -m 0644 "${SOURCE_DIR}/app.py" "${INSTALL_DIR}/app.py"
install -m 0644 "${SOURCE_DIR}/README.md" "${INSTALL_DIR}/README.md"
rm -rf "${INSTALL_DIR}/web"
install -d -m 0755 "${INSTALL_DIR}/web"
install -m 0644 "${SOURCE_DIR}/web/index.html" "${INSTALL_DIR}/web/index.html"
install -m 0644 "${SOURCE_DIR}/web/app.js" "${INSTALL_DIR}/web/app.js"
install -m 0644 "${SOURCE_DIR}/web/styles.css" "${INSTALL_DIR}/web/styles.css"

ENV_FILE="${ENV_DIR}/gatewatch.env"
cat > "${ENV_FILE}" <<ENV
GATEWATCH_HOST=${HOST}
GATEWATCH_PORT=${PORT}
GATEWATCH_DB=${DATA_DIR}/gatewatch.db
GATEWATCH_ALLOW_INSECURE_NETWORK=${ALLOW_NETWORK}
ENV
chown root:"${SERVICE_USER}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

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
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_UNIT}" >/dev/null

if [[ "${START_SERVICE}" == "1" ]]; then
  systemctl restart "${SERVICE_UNIT}"
  python3 - <<PY
import sys
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

Useful commands:
  systemctl status ${SERVICE_UNIT}
  journalctl -u ${SERVICE_UNIT} -f
  systemctl restart ${SERVICE_UNIT}
DONE
