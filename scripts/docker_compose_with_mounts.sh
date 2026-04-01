#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="$PROJECT_ROOT/docker/compose.full.yaml"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-atcode}"
declare -a EXTRA_MOUNTS=()
declare -a TARGET_SERVICES=()
declare -a COMPOSE_ARGS=()

usage() {
    cat <<'EOF'
Usage:
  ./scripts/docker_compose_with_mounts.sh [options] -- <docker compose args>

Options:
  -f, --file PATH         Compose file to use. Default: docker/compose.full.yaml
  -p, --project NAME      Compose project name. Default: COMPOSE_PROJECT_NAME or atcode
  -s, --service NAME      Service to receive extra mounts. Repeatable.
  -m, --mount SPEC        Extra mount for selected services. Repeatable.
                          SPEC formats:
                            /host/path
                            /host/path:/container/path
                            /host/path:/container/path:ro
                            /host/path:/container/path:rw

Examples:
  ./scripts/docker_compose_with_mounts.sh \
    --mount /data_gpu:/host/data_gpu:ro \
    --mount /share_data:/host/share_data:rw \
    -- up -d --build --no-deps frontend

  ./scripts/docker_compose_with_mounts.sh \
    --service backend \
    --mount /data_gpu:/host/data_gpu:ro \
    -- logs -f backend
EOF
}

detect_compose_command() {
    if docker compose version &>/dev/null; then
        COMPOSE_CMD=(docker compose)
        return 0
    fi

    if command -v docker-compose &>/dev/null; then
        COMPOSE_CMD=(docker-compose)
        return 0
    fi

    echo "Docker Compose not found. Install 'docker compose' plugin or 'docker-compose'." >&2
    exit 1
}

resolve_host_dir() {
    local raw_path="$1"
    if [[ "$raw_path" == /* ]]; then
        printf '%s\n' "$raw_path"
    else
        realpath -m "$(dirname "$COMPOSE_FILE")/$raw_path"
    fi
}

contains_service() {
    local service="$1"
    local item
    for item in "${TARGET_SERVICES[@]}"; do
        if [ "$item" = "$service" ]; then
            return 0
        fi
    done
    return 1
}

detect_default_services() {
    local service
    for service in backend frontend; do
        if rg -q "^  ${service}:" "$COMPOSE_FILE"; then
            TARGET_SERVICES+=("$service")
        fi
    done

    if [ ${#TARGET_SERVICES[@]} -eq 0 ] && [ ${#EXTRA_MOUNTS[@]} -gt 0 ]; then
        echo "No default mount targets found in $(realpath --relative-to="$PROJECT_ROOT" "$COMPOSE_FILE"). Use --service to specify target services." >&2
        exit 1
    fi
}

parse_mount_spec() {
    local spec="$1"
    local source target mode remainder

    if [[ "$spec" != *:* ]]; then
        source="$spec"
        target="/host$(printf '%s' "$source" | sed 's#//*#/#g')"
        mode="rw"
    else
        source="${spec%%:*}"
        remainder="${spec#*:}"
        if [[ "$remainder" == *:* ]]; then
            target="${remainder%:*}"
            mode="${remainder##*:}"
        else
            target="$remainder"
            mode="rw"
        fi
    fi

    if [[ "$source" != /* ]]; then
        echo "Mount source must be an absolute host path: $source" >&2
        exit 1
    fi

    if [ ! -e "$source" ]; then
        echo "Host mount path does not exist: $source" >&2
        exit 1
    fi

    if [[ "$target" != /* ]]; then
        echo "Mount target must be an absolute container path: $target" >&2
        exit 1
    fi

    if [[ "$mode" != "ro" && "$mode" != "rw" ]]; then
        echo "Mount mode must be ro or rw: $spec" >&2
        exit 1
    fi

    printf '%s\n%s\n%s\n' "$source" "$target" "$mode"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -f|--file)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        -p|--project)
            PROJECT_NAME="$2"
            shift 2
            ;;
        -s|--service)
            TARGET_SERVICES+=("$2")
            shift 2
            ;;
        -m|--mount)
            EXTRA_MOUNTS+=("$2")
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            COMPOSE_ARGS=("$@")
            break
            ;;
        *)
            COMPOSE_ARGS=("$@")
            break
            ;;
    esac
done

if [ ${#COMPOSE_ARGS[@]} -eq 0 ]; then
    usage
    exit 1
fi

if [[ "$COMPOSE_FILE" != /* ]]; then
    COMPOSE_FILE="$PROJECT_ROOT/$COMPOSE_FILE"
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Compose file not found: $COMPOSE_FILE" >&2
    exit 1
fi

if [ ${#TARGET_SERVICES[@]} -eq 0 ]; then
    detect_default_services
fi

export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"
mkdir -p "$(resolve_host_dir "${ATCODE_DATA_DIR:-../data}")"
mkdir -p "$(resolve_host_dir "${REDIS_DATA_DIR:-../data/redis}")"

detect_compose_command

if [ ${#EXTRA_MOUNTS[@]} -eq 0 ]; then
    exec "${COMPOSE_CMD[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "${COMPOSE_ARGS[@]}"
fi

OVERRIDE_FILE="$(mktemp /tmp/atcode-compose-extra-mounts.XXXXXX.yaml)"
cleanup() {
    rm -f "$OVERRIDE_FILE"
}
trap cleanup EXIT

{
    echo "services:"
    for service in "${TARGET_SERVICES[@]}"; do
        echo "  ${service}:"
        echo "    volumes:"
        for spec in "${EXTRA_MOUNTS[@]}"; do
            mapfile -t parsed < <(parse_mount_spec "$spec")
            printf "      - %s:%s:%s\n" "${parsed[0]}" "${parsed[1]}" "${parsed[2]}"
        done
    done
} > "$OVERRIDE_FILE"

"${COMPOSE_CMD[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" "${COMPOSE_ARGS[@]}"
