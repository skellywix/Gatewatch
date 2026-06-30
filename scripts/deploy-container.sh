#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_SOURCE_URL="https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz"

TARGET="${GATEWATCH_SSH_TARGET:-}"
BIND_IP="${GATEWATCH_BIND_IP:-127.0.0.1}"
HOST_PORT="${GATEWATCH_HOST_PORT:-8087}"
CONTAINER_NAME="${GATEWATCH_CONTAINER_NAME:-gatewatch-test}"
VOLUME_NAME="${GATEWATCH_VOLUME_NAME:-gatewatch-test-data}"
IMAGE_NAME="${GATEWATCH_IMAGE_NAME:-gatewatch:test}"
SOURCE_URL="${GATEWATCH_SOURCE_URL:-${DEFAULT_SOURCE_URL}}"
ADMIN_GROUP_CANONICAL="${GATEWATCH_ADMIN_GROUP_CANONICAL:-gcefcu.org/Users/Domain Admins}"
SESSION_SECRET="${GATEWATCH_SESSION_SECRET:-}"
ENTRA_TENANT_ID="${GATEWATCH_ENTRA_TENANT_ID:-}"
ENTRA_CLIENT_ID="${GATEWATCH_ENTRA_CLIENT_ID:-}"
ENTRA_CLIENT_SECRET="${GATEWATCH_ENTRA_CLIENT_SECRET:-}"
ENTRA_REDIRECT_URI="${GATEWATCH_ENTRA_REDIRECT_URI:-}"
RESET_DATA=0
VALIDATE_ONLY="${GATEWATCH_DEPLOY_VALIDATE_ONLY:-0}"

usage() {
  cat <<USAGE
Deploy Gatewatch as a locked-down Docker container on a remote Linux host.

Usage:
  scripts/deploy-container.sh --target user@host [options]

Required:
  --target TARGET               SSH target for the Docker host, for example user@host.

Options:
  --bind-ip IP                  Host IP to publish on. Default: ${BIND_IP}
  --host-port PORT              Host port to publish. Default: ${HOST_PORT}
  --container-name NAME         Container name. Default: ${CONTAINER_NAME}
  --volume-name NAME            Docker volume for SQLite data. Default: ${VOLUME_NAME}
  --image-name NAME             Docker image tag to build. Default: ${IMAGE_NAME}
  --source-url URL              Source tarball URL. Default: ${DEFAULT_SOURCE_URL}
  --admin-group-canonical NAME  AD/Entra group allowed to administer Gatewatch.
  --session-secret SECRET       Cookie signing secret. Generated on the host when omitted.
  --entra-tenant-id ID          Optional Microsoft Entra tenant ID.
  --entra-client-id ID          Optional Microsoft Entra client ID.
  --entra-client-secret SECRET  Optional Microsoft Entra client secret.
  --entra-redirect-uri URI      Optional Microsoft Entra redirect URI.
  --reset-data                  Remove the named Docker volume before starting.
  -h, --help                    Show this help.

Secret handling:
  Prefer GATEWATCH_SESSION_SECRET and GATEWATCH_ENTRA_CLIENT_SECRET environment
  variables for secrets. The helper sends values to the remote host over SSH
  stdin and does not echo them.

Examples:
  scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP
  scripts/deploy-container.sh --target user@host --bind-ip HOST_LAN_IP --reset-data
USAGE
}

die() {
  echo "error: $*" >&2
  exit 1
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  [[ -n "${value}" ]] || die "${flag} requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      require_value "$1" "${2:-}"
      TARGET="$2"
      shift 2
      ;;
    --bind-ip)
      require_value "$1" "${2:-}"
      BIND_IP="$2"
      shift 2
      ;;
    --host-port|--port)
      require_value "$1" "${2:-}"
      HOST_PORT="$2"
      shift 2
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
    --source-url)
      require_value "$1" "${2:-}"
      SOURCE_URL="$2"
      shift 2
      ;;
    --admin-group-canonical)
      require_value "$1" "${2:-}"
      ADMIN_GROUP_CANONICAL="$2"
      shift 2
      ;;
    --session-secret)
      require_value "$1" "${2:-}"
      SESSION_SECRET="$2"
      shift 2
      ;;
    --entra-tenant-id)
      require_value "$1" "${2:-}"
      ENTRA_TENANT_ID="$2"
      shift 2
      ;;
    --entra-client-id)
      require_value "$1" "${2:-}"
      ENTRA_CLIENT_ID="$2"
      shift 2
      ;;
    --entra-client-secret)
      require_value "$1" "${2:-}"
      ENTRA_CLIENT_SECRET="$2"
      shift 2
      ;;
    --entra-redirect-uri)
      require_value "$1" "${2:-}"
      ENTRA_REDIRECT_URI="$2"
      shift 2
      ;;
    --reset-data)
      RESET_DATA=1
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

[[ -n "${TARGET}" ]] || die "--target is required"
if ! [[ "${HOST_PORT}" =~ ^[0-9]+$ ]] || (( 10#${HOST_PORT} < 1 || 10#${HOST_PORT} > 65535 )); then
  die "--host-port must be a number from 1 to 65535"
fi

if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  echo "Deploy configuration validation passed"
  exit 0
fi

command -v ssh >/dev/null 2>&1 || die "ssh is required"

shell_quote() {
  printf "%q" "$1"
}

emit_remote_script() {
  printf "GATEWATCH_BIND_IP=%s\n" "$(shell_quote "${BIND_IP}")"
  printf "GATEWATCH_HOST_PORT=%s\n" "$(shell_quote "${HOST_PORT}")"
  printf "GATEWATCH_CONTAINER_NAME=%s\n" "$(shell_quote "${CONTAINER_NAME}")"
  printf "GATEWATCH_VOLUME_NAME=%s\n" "$(shell_quote "${VOLUME_NAME}")"
  printf "GATEWATCH_IMAGE_NAME=%s\n" "$(shell_quote "${IMAGE_NAME}")"
  printf "GATEWATCH_SOURCE_URL=%s\n" "$(shell_quote "${SOURCE_URL}")"
  printf "GATEWATCH_ADMIN_GROUP_CANONICAL=%s\n" "$(shell_quote "${ADMIN_GROUP_CANONICAL}")"
  printf "GATEWATCH_SESSION_SECRET=%s\n" "$(shell_quote "${SESSION_SECRET}")"
  printf "GATEWATCH_ENTRA_TENANT_ID=%s\n" "$(shell_quote "${ENTRA_TENANT_ID}")"
  printf "GATEWATCH_ENTRA_CLIENT_ID=%s\n" "$(shell_quote "${ENTRA_CLIENT_ID}")"
  printf "GATEWATCH_ENTRA_CLIENT_SECRET=%s\n" "$(shell_quote "${ENTRA_CLIENT_SECRET}")"
  printf "GATEWATCH_ENTRA_REDIRECT_URI=%s\n" "$(shell_quote "${ENTRA_REDIRECT_URI}")"
  printf "GATEWATCH_RESET_DATA=%s\n" "$(shell_quote "${RESET_DATA}")"
  cat <<'REMOTE'
set -Eeuo pipefail

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: $1 is required on the remote host" >&2
    exit 1
  }
}

need curl
need tar
need docker
need python3

build_dir="$(mktemp -d -t gatewatch-build.XXXXXX)"
cleanup() {
  rm -rf "${build_dir}"
}
trap cleanup EXIT

echo "Downloading Gatewatch source"
curl -fsSL "${GATEWATCH_SOURCE_URL}" -o "${build_dir}/source.tar.gz"
mkdir -p "${build_dir}/src"
tar -xzf "${build_dir}/source.tar.gz" -C "${build_dir}/src" --strip-components=1

echo "Building ${GATEWATCH_IMAGE_NAME}"
docker build -t "${GATEWATCH_IMAGE_NAME}" "${build_dir}/src" >/dev/null

if docker ps -a --format '{{.Names}}' | grep -Fxq "${GATEWATCH_CONTAINER_NAME}"; then
  echo "Removing existing container ${GATEWATCH_CONTAINER_NAME}"
  docker rm -f "${GATEWATCH_CONTAINER_NAME}" >/dev/null
fi

if [[ "${GATEWATCH_RESET_DATA}" == "1" ]]; then
  if docker volume ls --format '{{.Name}}' | grep -Fxq "${GATEWATCH_VOLUME_NAME}"; then
    echo "Removing data volume ${GATEWATCH_VOLUME_NAME}"
    docker volume rm "${GATEWATCH_VOLUME_NAME}" >/dev/null
  fi
fi

docker volume create "${GATEWATCH_VOLUME_NAME}" >/dev/null

if [[ -z "${GATEWATCH_SESSION_SECRET}" ]]; then
  GATEWATCH_SESSION_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
fi

run_args=(
  docker run -d
  --name "${GATEWATCH_CONTAINER_NAME}"
  --restart unless-stopped
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=64m
  --cap-drop ALL
  --security-opt no-new-privileges
  -p "${GATEWATCH_BIND_IP}:${GATEWATCH_HOST_PORT}:8087"
  -v "${GATEWATCH_VOLUME_NAME}:/data"
  -e GATEWATCH_HOST=0.0.0.0
  -e GATEWATCH_PORT=8087
  -e GATEWATCH_DB=/data/gatewatch.db
  -e GATEWATCH_CONFIG_FILE=/data/gatewatch.env
  -e GATEWATCH_ALLOW_INSECURE_NETWORK=1
  -e "GATEWATCH_SESSION_SECRET=${GATEWATCH_SESSION_SECRET}"
  -e "GATEWATCH_ADMIN_GROUP_CANONICAL=${GATEWATCH_ADMIN_GROUP_CANONICAL}"
)

append_env_if_set() {
  local name="$1"
  local value="$2"
  if [[ -n "${value}" ]]; then
    run_args+=(-e "${name}=${value}")
  fi
}

append_env_if_set GATEWATCH_ENTRA_TENANT_ID "${GATEWATCH_ENTRA_TENANT_ID}"
append_env_if_set GATEWATCH_ENTRA_CLIENT_ID "${GATEWATCH_ENTRA_CLIENT_ID}"
append_env_if_set GATEWATCH_ENTRA_CLIENT_SECRET "${GATEWATCH_ENTRA_CLIENT_SECRET}"
append_env_if_set GATEWATCH_ENTRA_REDIRECT_URI "${GATEWATCH_ENTRA_REDIRECT_URI}"

run_args+=("${GATEWATCH_IMAGE_NAME}")

echo "Starting ${GATEWATCH_CONTAINER_NAME}"
"${run_args[@]}" >/dev/null

probe_host="${GATEWATCH_BIND_IP}"
if [[ "${probe_host}" == "0.0.0.0" ]]; then
  probe_host="127.0.0.1"
fi

health_url="http://${probe_host}:${GATEWATCH_HOST_PORT}/healthz"
for _ in $(seq 1 40); do
  if curl -fsS "${health_url}" >/dev/null 2>&1; then
    echo "Health check passed: ${health_url}"
    docker ps --filter "name=^/${GATEWATCH_CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
    echo "Gatewatch is running at http://${GATEWATCH_BIND_IP}:${GATEWATCH_HOST_PORT}"
    exit 0
  fi
  sleep 1
done

docker logs --tail 80 "${GATEWATCH_CONTAINER_NAME}" >&2 || true
echo "error: health check did not pass: ${health_url}" >&2
exit 1
REMOTE
}

echo "Deploying Gatewatch to ${TARGET}"
echo "Container: ${CONTAINER_NAME}"
echo "Volume: ${VOLUME_NAME}"
echo "Bind: ${BIND_IP}:${HOST_PORT}"
echo "Source: ${SOURCE_URL}"
if [[ "${RESET_DATA}" == "1" ]]; then
  echo "Data reset: enabled for volume ${VOLUME_NAME}"
else
  echo "Data reset: disabled"
fi

emit_remote_script | ssh -o BatchMode=yes -o ConnectTimeout=10 "${TARGET}" "bash -s"
