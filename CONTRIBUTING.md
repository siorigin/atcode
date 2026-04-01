# Contributing to AtCode

Thank you for your interest in contributing to AtCode! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 18+
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) (recommended for Python dependency management)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/siorigin/atcode.git
cd atcode

# Start infrastructure
docker compose -f docker/compose.yaml up -d

# Install backend dependencies (with dev extras)
cp .env.example .env
uv sync --all-extras

# Install frontend dependencies
cd frontend && npm install && cd ..

# Start development servers
uv run ./scripts/start_api.sh --dev    # Backend with hot reload
bash ./scripts/start_front.sh --dev    # Frontend dev server (default frontend mode)
```

### Project Structure

```
atcode/
├── backend/
│   ├── agent/          # LangGraph agent orchestrators and tools
│   ├── api/            # FastAPI routes, middleware, services
│   ├── atlas_mcp/      # MCP server (standalone mode)
│   ├── core/           # Shared config, schemas, utilities
│   ├── graph/          # Knowledge graph builder, sync, embedder
│   ├── paper/          # Paper reading pipeline
│   ├── parser/         # Tree-sitter based multi-language parsers
│   └── tests/          # Backend test suite
├── docker/             # Compose files and Dockerfiles
├── frontend/           # Next.js 15 web application
├── docs/               # Technical documentation
└── scripts/            # Runtime, nginx, and helper scripts
    ├── nginx/          # Optional nginx config
    └── *.sh            # Local startup and helper scripts
```

## Code Style

- **Python**: We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting. Run `ruff check` and `ruff format` before committing.
- **TypeScript**: Standard Next.js conventions with TypeScript strict mode.

## Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests: `uv run pytest`
5. Commit with a clear message
6. Open a Pull Request

## Reporting Issues

Please use [GitHub Issues](https://github.com/siorigin/atcode/issues) to report bugs or request features. Include:

- Steps to reproduce
- Expected vs. actual behavior
- Environment details (OS, Python version, etc.)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
