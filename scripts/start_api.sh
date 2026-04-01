#!/bin/bash
# AtCode FastAPI Server Startup Script
#
# Usage:
#   ./scripts/start_api.sh              # Start with default backend settings (4 workers, no --reload)
#   ./scripts/start_api.sh --dev        # Development mode with auto-reload
#   ./scripts/start_api.sh --prod       # Explicit production-style startup
#   ./scripts/start_api.sh --host 0.0.0.0 --port 8006
#
# Configuration is read from .env file in project root

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}     AtCode FastAPI Server Launcher      ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

# Load .env from project root
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "Loading configuration from ${YELLOW}.env${NC}"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
else
    echo -e "${YELLOW}Warning: No .env file found. Using defaults.${NC}"
fi

# Default values
# Default startup is a production-style local run: multiple workers, no reload.
HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8008}"
# Multiple workers are now supported with timeout_worker_healthcheck=0 in run.py
WORKERS=4
RELOAD=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dev)
            echo -e "Mode: ${YELLOW}Development (hot-reload)${NC}"
            RELOAD="--reload"
            WORKERS=1
            LOG_LEVEL="DEBUG"
            LOGURU_LEVEL="DEBUG"
            shift
            ;;
        --prod)
            echo -e "Mode: ${YELLOW}Production${NC}"
            WORKERS="${WORKERS:-4}"
            shift
            ;;
        --no-uv)
            echo -e "Disable uv run (use current python environment)${NC}"
            USE_UV=0
            shift
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            API_PORT="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Usage: $0 [--dev|--prod] [--no-uv] [--host HOST] [--port PORT] [--workers N]"
            exit 1
            ;;
    esac
done

# Navigate to backend directory
cd "$BACKEND_DIR"

# Check Python
if ! command -v python &> /dev/null; then
    echo -e "${RED}Error: Python is not installed${NC}"
    exit 1
fi

PYTHON_VERSION=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "Python version: ${YELLOW}$PYTHON_VERSION${NC}"

echo ""
echo -e "Memgraph: ${MEMGRAPH_HOST:-localhost}:${MEMGRAPH_PORT:-7687}"
echo ""
echo -e "Server Configuration:"
echo -e "  Host:    ${YELLOW}$HOST${NC}"
echo -e "  Port:    ${YELLOW}$API_PORT${NC}"
echo -e "  Workers: ${YELLOW}$WORKERS${NC}"
echo ""

# Check dependencies
echo "Checking dependencies..."
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}Installing FastAPI dependencies...${NC}"
    pip install fastapi uvicorn python-multipart
fi

echo -e "${GREEN}Starting server...${NC}"
echo ""

# Export environment for child process
export PYTHONUNBUFFERED=1
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export LOGURU_LEVEL="${LOGURU_LEVEL:-INFO}"

# Export variables needed by the application
export API_PORT
export API_URL
export ALLOWED_ORIGINS
export MEMGRAPH_HOST
export MEMGRAPH_PORT
export JWT_SECRET
echo "WORKERS: $WORKERS"
# Start the server
# Use gunicorn for multi-worker mode (more stable than uvicorn's built-in multiprocessing)
# Use uvicorn directly for single worker mode
if [ "$WORKERS" -gt 1 ]; then
    echo -e "${CYAN}Using gunicorn with $WORKERS uvicorn workers${NC}"
    GUNICORN_ARGS=(
        api.main:app
        -w "$WORKERS"
        -k uvicorn.workers.UvicornWorker
        --timeout 0
        --graceful-timeout 30
        -b "$HOST:$API_PORT"
        --access-logfile -
        --error-logfile -
        --log-level "${LOG_LEVEL,,}"
    )

    # Prefer project-managed environment (uv) so global gunicorn is not required.
    # However, uv editable install can fail in some environments, so allow bypassing it.
    if [ "${USE_UV:-1}" -eq 1 ] && command -v uv >/dev/null 2>&1; then
        uv run gunicorn "${GUNICORN_ARGS[@]}"
    elif command -v gunicorn >/dev/null 2>&1; then
        gunicorn "${GUNICORN_ARGS[@]}"
    else
        echo -e "${YELLOW}gunicorn not found in current environment. Falling back to single-worker uvicorn.${NC}"
        python -m api.run --host "$HOST" --port "$API_PORT" --workers 1 $RELOAD
    fi
else
    echo -e "${CYAN}Using uvicorn directly (single worker)${NC}"
    python -m api.run --host "$HOST" --port "$API_PORT" --workers 1 $RELOAD
fi
