#!/usr/bin/env bash
# AtCode — unified bootstrap script
# Usage:
#   ./atcode.sh up [dev|prod]        # check/install deps, start infra, install deps, then start backend + frontend
#   ./atcode.sh refresh [target] [dev|prod]   # restart app services only (keep memgraph/redis running)
#   ./atcode.sh down       # stop everything
#   ./atcode.sh status     # show running state
#   ./atcode.sh logs       # tail backend + frontend logs
#
# The script reuses scripts/start_api.sh / scripts/start_front.sh unchanged.
# Backend starts with start_api.sh defaults; frontend starts via start_front.sh --no-install.

set -euo pipefail

# ── paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PID_DIR="$PROJECT_ROOT/.run"
LOG_DIR="$PROJECT_ROOT/data/logs"
COMPOSE_FILE="$PROJECT_ROOT/docker/compose.yaml"
COMPOSE_PROJECT_NAME_DEFAULT="atcode"
DOCKER_COMPOSE_CMD=()
DOCKER_COMPOSE_LABEL=""

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*"; }
header(){ echo -e "\n${CYAN}── $* ──${NC}"; }

resolve_compose_host_path() {
    local raw_path="$1"
    if [[ "$raw_path" == /* ]]; then
        printf '%s\n' "$raw_path"
    else
        realpath -m "$(dirname "$COMPOSE_FILE")/$raw_path"
    fi
}

resolve_frontend_mode() {
    local requested="${1:-${FRONTEND_MODE:-prod}}"
    case "$requested" in
        ""|prod|--prod)
            printf 'prod\n'
            ;;
        dev|--dev)
            printf 'dev\n'
            ;;
        *)
            err "Unknown frontend mode: $requested"
            echo "Use 'prod' or 'dev'."
            return 1
            ;;
    esac
}

# ── load .env ────────────────────────────────────────────────────────────────
load_env() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        if [ -f "$PROJECT_ROOT/.env.example" ]; then
            warn ".env not found. Creating from .env.example ..."
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
            warn "Please edit .env and fill in at least LLM_API_KEY, then re-run."
            exit 1
        fi
        err ".env not found.  Copy .env.example and fill in required values:"
        echo "    cp .env.example .env"
        exit 1
    fi
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a

    # Defaults after sourcing
    PORT="${PORT:-3007}"
    API_PORT="${API_PORT:-8008}"
    DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX:-}"
    if [ -n "$DOCKER_IMAGE_PREFIX" ] && [[ "$DOCKER_IMAGE_PREFIX" != */ ]]; then
        DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX}/"
    fi
    DOCKER_IMAGE_FALLBACK_PREFIX="${DOCKER_IMAGE_FALLBACK_PREFIX:-docker.1ms.run/}"
    if [ -n "$DOCKER_IMAGE_FALLBACK_PREFIX" ] && [[ "$DOCKER_IMAGE_FALLBACK_PREFIX" != */ ]]; then
        DOCKER_IMAGE_FALLBACK_PREFIX="${DOCKER_IMAGE_FALLBACK_PREFIX}/"
    fi
    LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/data/logs}"
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$COMPOSE_PROJECT_NAME_DEFAULT}"
    HOST_UID="${HOST_UID:-$(id -u)}"
    HOST_GID="${HOST_GID:-$(id -g)}"
    FRONTEND_MODE="${FRONTEND_MODE:-prod}"
    ATCODE_DATA_DIR="${ATCODE_DATA_DIR:-../data}"
    REDIS_DATA_DIR="${REDIS_DATA_DIR:-../data/redis}"
    export DOCKER_IMAGE_PREFIX
    export DOCKER_IMAGE_FALLBACK_PREFIX
    export LOG_DIR
    export COMPOSE_PROJECT_NAME
    export HOST_UID
    export HOST_GID
    export FRONTEND_MODE
    export ATCODE_DATA_DIR
    export REDIS_DATA_DIR
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    mkdir -p "$(resolve_compose_host_path "$ATCODE_DATA_DIR")" 2>/dev/null || true
    mkdir -p "$(resolve_compose_host_path "$REDIS_DATA_DIR")" 2>/dev/null || true
}

# ── auto-install helpers ─────────────────────────────────────────────────────
#
# Strategy:
#   uv, nvm/node/npm  — user-space, no root needed, auto-install
#   cmake/gcc/make    — need apt/yum, auto-install with sudo if available
#   docker            — too platform-specific, print instructions and bail

has_cmd() { command -v "$1" &>/dev/null; }

detect_compose_command() {
    if [ ${#DOCKER_COMPOSE_CMD[@]} -gt 0 ]; then
        return 0
    fi

    if docker compose version &>/dev/null; then
        DOCKER_COMPOSE_CMD=(docker compose)
        DOCKER_COMPOSE_LABEL="docker compose"
        return 0
    fi

    if has_cmd docker-compose; then
        DOCKER_COMPOSE_CMD=(docker-compose)
        DOCKER_COMPOSE_LABEL="docker-compose"
        return 0
    fi

    return 1
}

compose_version() {
    detect_compose_command || return 1
    "${DOCKER_COMPOSE_CMD[@]}" version --short 2>/dev/null || "${DOCKER_COMPOSE_CMD[@]}" version 2>/dev/null | head -1
}

docker_compose() {
    if ! detect_compose_command; then
        err "Docker Compose not found. Install 'docker compose' plugin or 'docker-compose'."
        return 1
    fi
    "${DOCKER_COMPOSE_CMD[@]}" -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

# Reload shell paths after an install so the new binary is visible immediately
reload_path() {
    # uv
    [ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env" 2>/dev/null || true
    [ -d "$HOME/.cargo/bin" ]     && export PATH="$HOME/.cargo/bin:$PATH"
    [ -d "$HOME/.local/bin" ]     && export PATH="$HOME/.local/bin:$PATH"
    # nvm
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" 2>/dev/null || true
}

install_uv() {
    header "Installing uv (Python package manager)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    reload_path
    if has_cmd uv; then
        info "uv installed: $(uv --version)"
    else
        err "uv install script finished but 'uv' not found in PATH."
        err "Try: source \$HOME/.local/bin/env   then re-run this script."
        exit 1
    fi
}

install_node() {
    header "Installing Node.js via nvm"
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        echo -e "${CYAN}Installing nvm ...${NC}"
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
    fi
    # shellcheck disable=SC1091
    source "$NVM_DIR/nvm.sh"
    nvm install --lts
    nvm use --lts
    reload_path
    if has_cmd node && has_cmd npm; then
        info "node $(node -v), npm $(npm -v)"
    else
        err "nvm install finished but node/npm not found."
        exit 1
    fi
}

install_build_tools() {
    header "Installing build tools (cmake, gcc, make)"
    if has_cmd apt-get; then
        sudo apt-get update -qq
        sudo apt-get install -y --no-install-recommends cmake gcc g++ make
    elif has_cmd yum; then
        sudo yum install -y cmake gcc gcc-c++ make
    elif has_cmd dnf; then
        sudo dnf install -y cmake gcc gcc-c++ make
    else
        warn "Unknown package manager. Please install cmake, gcc, make manually."
        return 1
    fi
    info "Build tools installed"
}

install_docker() {
    header "Installing Docker"
    if [[ "$(uname)" != "Linux" ]]; then
        err "Automatic Docker install is only supported on Linux."
        err "Install manually: https://docs.docker.com/engine/install/"
        exit 1
    fi
    echo -e "${CYAN}Running Docker's official install script (requires sudo) ...${NC}"
    curl -fsSL https://get.docker.com | sh
    # Add current user to docker group so we can run without sudo
    if ! groups "$USER" | grep -q docker; then
        sudo usermod -aG docker "$USER"
        warn "Added $USER to docker group. You may need to log out and back in,"
        warn "or run 'newgrp docker' before docker commands work without sudo."
    fi
    # Start dockerd if not running
    if ! docker info &>/dev/null; then
        sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true
    fi
    if has_cmd docker; then
        info "Docker installed: $(docker --version)"
    else
        err "Docker install finished but 'docker' not found."
        exit 1
    fi
}

# ── dependency check + auto-install ──────────────────────────────────────────
check_deps() {
    header "Checking dependencies"

    # ─ docker ─
    if ! has_cmd docker; then
        warn "docker not found"
        install_docker
    else
        info "docker $(docker --version | head -1)"
    fi

    if ! detect_compose_command; then
        warn "Docker Compose not found — attempting install of docker compose plugin"
        if has_cmd apt-get; then
            sudo apt-get update -qq && sudo apt-get install -y docker-compose-plugin
        else
            err "Could not auto-install Docker Compose."
            err "Install either 'docker compose' plugin or the legacy 'docker-compose' binary."
            exit 1
        fi

        DOCKER_COMPOSE_CMD=()
        DOCKER_COMPOSE_LABEL=""
        if ! detect_compose_command; then
            err "Docker Compose install finished but no Compose command is available."
            exit 1
        fi
    fi
    info "${DOCKER_COMPOSE_LABEL} $(compose_version 2>/dev/null || echo 'ok')"

    # ─ uv ─
    reload_path
    if ! has_cmd uv; then
        warn "uv not found"
        install_uv
    else
        info "uv $(uv --version 2>/dev/null || echo 'ok')"
    fi

    # ─ node / npm ─
    reload_path
    if ! has_cmd node || ! has_cmd npm; then
        warn "node/npm not found"
        install_node
    else
        info "node $(node -v), npm $(npm -v)"
    fi

    # ─ build tools (Linux) ─
    if [[ "$(uname)" == "Linux" ]]; then
        local missing_tools=()
        for tool in cmake gcc make; do
            if ! has_cmd "$tool"; then
                missing_tools+=("$tool")
            fi
        done
        if [ ${#missing_tools[@]} -gt 0 ]; then
            warn "Missing build tools: ${missing_tools[*]}"
            if has_cmd sudo; then
                install_build_tools || warn "Could not install build tools. Some native extensions may fail."
            else
                warn "No sudo available. Install manually: ${missing_tools[*]}"
            fi
        else
            info "build tools: cmake, gcc, make"
        fi
    fi

    echo ""
    info "All dependencies ready"
}

# ── env sanity ───────────────────────────────────────────────────────────────
check_env() {
    header "Validating .env"
    local problems=0

    # Check for placeholder values that user forgot to fill
    for key in LLM_API_KEY; do
        val="$(printenv "$key" 2>/dev/null || true)"
        if [ -z "$val" ] || [[ "$val" == your-* ]] || [[ "$val" == change-me* ]]; then
            warn "$key looks unconfigured (value: '${val:-<empty>}')"
            problems=1
        fi
    done

    if [ "$problems" -ne 0 ]; then
        warn "Some .env values may need attention — the app may still start but LLM features will not work."
    else
        info ".env looks good"
    fi
}

# ── infrastructure ───────────────────────────────────────────────────────────
start_infra() {
    header "Starting infrastructure (memgraph, redis, lab)"
    cd "$PROJECT_ROOT"

    local compose_prefix="${DOCKER_IMAGE_PREFIX:-}"
    if [ -n "${DOCKER_IMAGE_PREFIX:-}" ]; then
        info "Using image mirror prefix: ${DOCKER_IMAGE_PREFIX}"
    fi

    # Start core services first (memgraph + redis), then lab separately.
    # lab is optional (a debug UI) — if its port is occupied we warn and continue.
    if ! DOCKER_IMAGE_PREFIX="$compose_prefix" docker_compose up -d memgraph redis; then
        if [ -z "$compose_prefix" ] && [ -n "${DOCKER_IMAGE_FALLBACK_PREFIX:-}" ]; then
            warn "Pull from Docker Hub failed, retrying with mirror: ${DOCKER_IMAGE_FALLBACK_PREFIX}"
            compose_prefix="$DOCKER_IMAGE_FALLBACK_PREFIX"
            DOCKER_IMAGE_PREFIX="$compose_prefix" docker_compose up -d memgraph redis
        else
            err "Failed to start core infrastructure (memgraph + redis)."
            return 1
        fi
    fi

    if ! DOCKER_IMAGE_PREFIX="$compose_prefix" docker_compose up -d lab 2>/dev/null; then
        warn "Memgraph Lab failed to start (port ${LAB_PORT:-3000} likely in use)."
        warn "This is optional and won't affect AtCode. Set LAB_PORT in .env to change."
    fi
    info "Core infrastructure is up (memgraph + redis)"
}

stop_infra() {
    cd "$PROJECT_ROOT"
    if docker_compose ps --quiet 2>/dev/null | grep -q .; then
        docker_compose down
        info "Infrastructure stopped"
    else
        info "Infrastructure already stopped"
    fi
}

# ── python deps ──────────────────────────────────────────────────────────────
install_python_deps() {
    header "Installing Python dependencies (uv sync)"
    cd "$PROJECT_ROOT"
    uv sync --extra production --extra mcp --extra paper --extra treesitter-full --extra fastapi --extra redis
    info "Python dependencies installed"
}

# ── frontend deps ────────────────────────────────────────────────────────────
install_frontend_deps() {
    header "Installing frontend dependencies"
    cd "$PROJECT_ROOT/frontend"
    if [ ! -d "node_modules" ]; then
        npm install
        info "Frontend dependencies installed"
    else
        info "Frontend dependencies already present (use npm install to update)"
    fi
}

# ── start / stop backend & frontend ─────────────────────────────────────────
mkdir -p "$PID_DIR" 2>/dev/null || true

start_backend() {
    header "Starting backend (API)"
    if [ -f "$PID_DIR/backend.pid" ] && kill -0 "$(cat "$PID_DIR/backend.pid")" 2>/dev/null; then
        info "Backend already running (PID $(cat "$PID_DIR/backend.pid"))"
        return
    fi
    cd "$PROJECT_ROOT"
    nohup bash "$PROJECT_ROOT/scripts/start_api.sh" > "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"
    info "Backend started (PID $!) — log: $LOG_DIR/backend.log"
}

start_frontend() {
    local frontend_mode
    frontend_mode="$(resolve_frontend_mode "${1:-}")" || return 1
    header "Starting frontend"
    if [ -f "$PID_DIR/frontend.pid" ] && kill -0 "$(cat "$PID_DIR/frontend.pid")" 2>/dev/null; then
        info "Frontend already running (PID $(cat "$PID_DIR/frontend.pid"))"
        return
    fi
    cd "$PROJECT_ROOT"
    nohup bash "$PROJECT_ROOT/scripts/start_front.sh" "--$frontend_mode" --no-install > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"
    info "Frontend started in ${frontend_mode} mode (PID $!) — log: $LOG_DIR/frontend.log"
}

stop_process() {
    local name="$1"
    local pidfile="$PID_DIR/${name}.pid"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            # Kill the process group so child processes also stop
            kill -- -"$(ps -o pgid= -p "$pid" | tr -d ' ')" 2>/dev/null || kill "$pid" 2>/dev/null || true
            info "$name stopped (was PID $pid)"
        else
            info "$name was not running"
        fi
        rm -f "$pidfile"
    else
        info "$name: no pid file found"
    fi
}

# ── commands ─────────────────────────────────────────────────────────────────
cmd_up() {
    load_env
    local frontend_mode
    frontend_mode="$(resolve_frontend_mode "${1:-}")" || return 1
    check_deps
    check_env
    start_infra
    install_python_deps
    install_frontend_deps
    start_backend
    start_frontend "$frontend_mode"

    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}          AtCode is running!             ${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo -e "  Frontend:  ${CYAN}http://localhost:${PORT}${NC}"
    echo -e "  Backend:   ${CYAN}http://localhost:${API_PORT}${NC}"
    echo ""
    echo -e "  Logs:      ${YELLOW}./atcode.sh logs${NC}"
    echo -e "  Stop:      ${YELLOW}./atcode.sh down${NC}"
    echo ""
}

cmd_down() {
    load_env
    header "Stopping AtCode"
    stop_process frontend
    stop_process backend
    stop_infra
    info "Everything stopped"
}

cmd_refresh() {
    load_env

    local target="${1:-all}"
    local frontend_mode="${2:-}"
    header "Refreshing AtCode application services"

    case "$target" in
        all)
            stop_process frontend
            stop_process backend
            start_backend
            start_frontend "$frontend_mode"
            ;;
        backend)
            stop_process backend
            start_backend
            ;;
        frontend)
            stop_process frontend
            start_frontend "$frontend_mode"
            ;;
        *)
            err "Unknown refresh target: $target"
            echo "Usage: $0 refresh [backend|frontend|all] [dev|prod]"
            return 1
            ;;
    esac

    info "Infrastructure unchanged (memgraph + redis untouched)"
}

cmd_status() {
    load_env
    header "AtCode Status"

    # Backend
    if [ -f "$PID_DIR/backend.pid" ] && kill -0 "$(cat "$PID_DIR/backend.pid")" 2>/dev/null; then
        info "Backend:       running (PID $(cat "$PID_DIR/backend.pid")) — http://localhost:${API_PORT}"
    else
        warn "Backend:       not running"
    fi

    # Frontend
    if [ -f "$PID_DIR/frontend.pid" ] && kill -0 "$(cat "$PID_DIR/frontend.pid")" 2>/dev/null; then
        info "Frontend:      running (PID $(cat "$PID_DIR/frontend.pid")) — http://localhost:${PORT}"
    else
        warn "Frontend:      not running"
    fi

    # Infrastructure
    echo ""
    cd "$PROJECT_ROOT"
    docker_compose ps 2>/dev/null || warn "Docker Compose not reachable"
}

cmd_logs() {
    load_env
    echo -e "${CYAN}Tailing backend + frontend logs (Ctrl-C to stop)${NC}"
    echo ""
    tail -f "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log" 2>/dev/null || {
        warn "No log files found yet. Start AtCode first: ./atcode.sh up"
    }
}

# ── main ─────────────────────────────────────────────────────────────────────
case "${1:-}" in
    up)     shift; cmd_up "$@" ;;
    refresh) shift; cmd_refresh "$@" ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    logs)   cmd_logs ;;
    *)
        echo "Usage: $0 {up|refresh|down|status|logs}"
        echo ""
        echo "  up [dev|prod]      Check/install deps, start infra + backend + frontend"
        echo "  refresh Restart backend/frontend only; keep docker infra running"
        echo "          Optional target: backend | frontend | all"
        echo "          Optional frontend mode: dev | prod (default: prod)"
        echo "  down    Stop everything (frontend, backend, docker infra)"
        echo "  status  Show running state"
        echo "  logs    Tail backend and frontend logs"
        exit 1
        ;;
esac
