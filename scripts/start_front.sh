#!/bin/bash
# AtCode Frontend (Next.js) Startup Script
#
# Usage:
#   ./scripts/start_front.sh            # Start frontend in default dev mode; install deps if needed
#   ./scripts/start_front.sh --dev      # Dev mode (default)
#   ./scripts/start_front.sh --prod     # Prod mode (build + start)
#   ./scripts/start_front.sh --host 0.0.0.0 --port 3006
#   ./scripts/start_front.sh --no-install  # Skip npm install check when deps are already present
#
# Config is read from .env in project root.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

MODE="dev"
HOST="${FRONTEND_HOST:-0.0.0.0}"
NO_INSTALL=false

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}     AtCode Frontend (Next.js) Launcher  ${NC}"
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

# Re-apply defaults if .env didn't set them
HOST="${FRONTEND_HOST:-$HOST}"
PORT="${PORT:-3007}"

# ---------------------------------------------------------------------------
# Make backend config available to the browser bundle.
#
# Next.js only exposes env vars prefixed with NEXT_PUBLIC_ to client-side code.
# The project .env uses API_PORT, so we bridge it here to avoid falling back
# to the default port (8009) in client code.
# ---------------------------------------------------------------------------
if [ -n "${API_PORT:-}" ] && [ -z "${NEXT_PUBLIC_API_PORT:-}" ]; then
  export NEXT_PUBLIC_API_PORT="$API_PORT"
fi

# Do NOT set NEXT_PUBLIC_API_URL here — the client-side code in api-config.ts
# dynamically uses window.location.hostname so that both localhost and remote IP
# access work automatically. Only set NEXT_PUBLIC_API_URL in .env if you need a
# specific override (e.g. /backapi via nginx reverse proxy).

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --dev)
      MODE="dev"
      shift
      ;;
    --prod)
      MODE="prod"
      shift
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --no-install)
      NO_INSTALL=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--dev|--prod] [--host HOST] [--port PORT] [--no-install]"
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      echo "Usage: $0 [--dev|--prod] [--host HOST] [--port PORT] [--no-install]"
      exit 1
      ;;
  esac
done

# Validate
if [ ! -d "$FRONTEND_DIR" ]; then
  echo -e "${RED}Error: frontend directory not found at: $FRONTEND_DIR${NC}"
  exit 1
fi

if ! command -v node &> /dev/null; then
  echo -e "${RED}Error: node is not installed${NC}"
  exit 1
fi

if ! command -v npm &> /dev/null; then
  echo -e "${RED}Error: npm is not installed${NC}"
  exit 1
fi

cd "$FRONTEND_DIR"

if [ "$NO_INSTALL" = "false" ]; then
  if [ ! -d "node_modules" ]; then
    echo -e "${CYAN}Installing frontend dependencies...${NC}"
    npm install
  else
    echo -e "${GREEN}✓ Frontend dependencies already installed${NC}"
  fi
else
  echo -e "${YELLOW}Skipping dependency install (--no-install)${NC}"
fi

echo ""
echo -e "Frontend Configuration:"
echo -e "  Mode: ${YELLOW}$MODE${NC}"
echo -e "  Host: ${YELLOW}$HOST${NC}"
echo -e "  Port: ${YELLOW}$PORT${NC}"
echo ""
echo -e "${GREEN}Starting frontend...${NC}"
echo -e "  URL: ${CYAN}http://localhost:$PORT${NC}"
echo ""

# Next.js CLI flags: -p/--port, -H/--hostname
if [ "$MODE" = "prod" ]; then
  # Ensure build exists; build if missing
  if [ ! -d ".next" ]; then
    echo -e "${YELLOW}No .next build found; running build...${NC}"
    npm run build
  fi

  # Copy static files to standalone directory (required by standalone mode)
  if [ -d ".next/standalone/frontend" ]; then
    echo -e "${CYAN}Copying static files to standalone directory...${NC}"
    cp -r .next/static .next/standalone/frontend/.next/static
    [ -d "public" ] && cp -r public .next/standalone/frontend/public
    # Copy WebSocket proxy wrapper
    [ -f "start.js" ] && cp start.js .next/standalone/frontend/start.js
  fi

  # standalone mode: use start.js (wraps server.js with WebSocket proxy support)
  export PORT="$PORT"
  export HOSTNAME="$HOST"
  # Pass backend config to standalone runtime (NEXT_PUBLIC_* are inlined at build time
  # by Webpack/Turbopack, but the standalone server.js may still need them at runtime
  # for server-side rendering and API routes)
  export API_PORT="${API_PORT:-8008}"
  export NEXT_PUBLIC_API_PORT="${NEXT_PUBLIC_API_PORT:-$API_PORT}"
  export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-}"
  export NEXT_PUBLIC_MCP_URL="${NEXT_PUBLIC_MCP_URL:-}"
  exec node .next/standalone/frontend/start.js
else
  exec npm run dev -- -p "$PORT" -H "$HOST"
fi
