# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import sys
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

# Add parent directory to path for imports when running as standalone
_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from loguru import logger  # noqa: E402

# Configure loguru log level early (before any module emits logs)
# This ensures LOG_LEVEL from .env takes effect globally
_early_log_level = os.getenv("LOG_LEVEL", "INFO")
logger.remove()
logger.add(sys.stderr, level=_early_log_level)

from api.dependencies import (  # noqa: E402
    cleanup_dependencies,
    get_config,
    initialize_dependencies,
)
from api.cors import build_cors_settings  # noqa: E402
from api.middleware.auth import AuthMiddleware  # noqa: E402
from api.routes import (  # noqa: E402
    chat,
    docs,
    feedback,
    folders,
    health,
    overview,
    papers,
    regenerate,
    repos,
    sessions,
    tasks,
)
from api.routes import config as config_routes  # noqa: E402
from api.routes import graph as graph_routes  # noqa: E402
from api.routes import sync as sync_routes  # noqa: E402
from api.services.build_scheduler import (  # noqa: E402
    get_build_scheduler,
    initialize_build_scheduler,
)
from api.services.sync_state_store import (  # noqa: E402
    cleanup_sync_state_store,
    initialize_sync_state_store,
)
from api.services.task_queue import initialize_task_manager  # noqa: E402

# Application version
VERSION = "2.0.0"

# Track startup time
_startup_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info(f"Starting AtCode API v{VERSION}...")

    # Load configuration
    config = get_config()

    # Setup logging - use try/except for graceful fallback
    try:
        from core.config import resolve_project_path
        from core.logger import setup_logging

        setup_logging(
            log_dir=resolve_project_path(config.get("log_dir", "./data/logs")),
            log_level=config.get("log_level", "INFO"),
            json_format=config.get("log_json", False),
        )
    except ImportError:
        # Fallback to basic loguru configuration
        logger.info("Using default loguru configuration")

    # Initialize dependencies
    await initialize_dependencies(config)

    # Initialize background task manager with Redis persistence
    # Redis provides single source of truth for multi-worker environments
    # Falls back to in-memory if Redis is unavailable
    from core.config import settings

    redis_url = getattr(
        settings, "REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

    task_manager = initialize_task_manager(store_type="redis", redis_url=redis_url)
    redis_connected = await task_manager.initialize_redis(redis_url)

    if redis_connected:
        logger.info(f"✅ Background task manager initialized with Redis: {redis_url}")
    else:
        logger.warning(
            "⚠️ Redis unavailable, task manager using in-memory storage (tasks will not persist across restarts)"
        )

    # Clean up any stale/zombie tasks from previous server instances
    cleaned_count = await task_manager.cleanup_stale_tasks()
    if cleaned_count > 0:
        logger.info(f"🧹 Cleaned up {cleaned_count} stale task(s) from previous run")

    # Start the task queue processor for concurrency control
    await task_manager.start_queue_processor()
    logger.info("✅ Task queue processor started")

    # Start the cleanup job for old tasks (runs every 24 hours)
    await task_manager.start_cleanup_job(cleanup_interval_hours=24)
    logger.info("✅ Task cleanup job started (interval: 24 hours)")

    # Initialize build scheduler for queue-based graph builds
    # This ensures only one graph build runs at a time to prevent CPU saturation
    await initialize_build_scheduler()
    logger.info("✅ Build scheduler initialized (queue-based builds enabled)")

    # Initialize sync state store for multi-worker file monitoring
    # Uses Redis to share watching task state across workers
    try:
        sync_store = await initialize_sync_state_store(redis_url)
        if sync_store and sync_store.is_connected:
            logger.info(
                "✅ Sync state store initialized (multi-worker sync support enabled)"
            )
        else:
            logger.warning(
                "⚠️ Sync state store not connected (sync state will not be shared across workers)"
            )
    except Exception as e:
        logger.warning(f"Failed to initialize sync state store: {e}")

    # Initialize Redis Pub/Sub for cross-worker task broadcasting
    # In multi-worker environments, this ensures task updates reach all clients
    # regardless of which worker handles the task
    if os.getenv("ENABLE_PUBSUB", "true").lower() == "true":
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            from api.services.task_pubsub import initialize_pubsub

            pubsub_manager = await initialize_pubsub(redis_url)
            if pubsub_manager and pubsub_manager.is_connected:
                logger.info(
                    "✅ Redis Pub/Sub initialized for cross-worker task updates"
                )
            else:
                logger.info(
                    "ℹ️ Redis Pub/Sub not available (falling back to local WebSocket only)"
                )
        except ImportError:
            logger.info(
                "ℹ️ Redis package not available (falling back to local WebSocket only)"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize Redis Pub/Sub: {e}")

    logger.info(f"AtCode API v{VERSION} started successfully")
    logger.info(
        f"Configuration: mode={config.get('api_mode')}, storage={config.get('storage_backend')}, cache={config.get('cache_backend')}"
    )

    # Auto-crawl last 7 days of HF daily papers in background
    async def _auto_crawl_papers():
        try:
            from paper.hf_crawler import crawl_recent_days

            result = await crawl_recent_days(days=7)
            logger.info(f"Papers auto-crawl: {result}")
        except Exception as e:
            logger.warning(f"Papers auto-crawl failed: {e}")

    asyncio.create_task(_auto_crawl_papers())
    logger.info("Papers auto-crawl started (last 7 days, background)")

    # Initialize MCP lifespan for streamable-http transport
    # This is required because streamable-http needs an active task group
    mcp_lifespan_cm = None
    try:
        from api.routes.mcp_sse import mcp_app

        if hasattr(mcp_app, "lifespan"):
            mcp_lifespan_cm = mcp_app.lifespan(mcp_app)
            await mcp_lifespan_cm.__aenter__()
            logger.info("✅ MCP lifespan initialized (streamable-http transport ready)")
    except ImportError:
        logger.debug("MCP module not available")
    except Exception as e:
        logger.warning(f"Failed to initialize MCP lifespan: {e}")
        mcp_lifespan_cm = None

    # Store for cleanup
    app.state.mcp_lifespan_cm = mcp_lifespan_cm

    # Pre-fill graph cache so the first user doesn't wait for Memgraph queries
    try:
        from api.routes.graph import warm_graph_cache
        await warm_graph_cache()
    except Exception as e:
        logger.warning(f"Graph cache warmup failed (non-fatal): {e}")

    yield

    # Shutdown
    logger.info("Shutting down AtCode API...")

    # Wait for in-flight graph builds to complete (with timeout)
    # This prevents "child process died" errors by allowing active builds
    # to finish gracefully before worker termination
    shutdown_timeout = 30.0  # Wait up to 30 seconds for builds to complete

    try:
        from api.routes.graph import _active_updaters, _updater_lock

        start_time = time.time()
        active_count = 0

        async with _updater_lock:
            active_count = len(_active_updaters)

        if active_count > 0:
            logger.info(
                f"Waiting for {active_count} in-flight graph build(s) to complete..."
            )

            while time.time() - start_time < shutdown_timeout:
                await asyncio.sleep(0.5)

                async with _updater_lock:
                    active_count = len(_active_updaters)
                    if active_count == 0:
                        break

                elapsed = time.time() - start_time
                if int(elapsed) % 5 == 0 and elapsed > 0:  # Log every 5 seconds
                    logger.info(
                        f"Still waiting for {active_count} build(s)... ({elapsed:.1f}s elapsed)"
                    )

            # Check final state
            async with _updater_lock:
                active_count = len(_active_updaters)

            if active_count > 0:
                logger.warning(
                    f"Shutdown timeout ({shutdown_timeout}s): {active_count} build(s) still running. "
                    f"Cancelling remaining builds..."
                )
                # Cancel remaining builds
                async with _updater_lock:
                    for job_id, updater in _active_updaters.items():
                        logger.info(f"Cancelling build: {job_id}")
                        updater.cancel()
            else:
                logger.info("All graph builds completed, shutdown clean")
        else:
            logger.info("No in-flight builds, proceeding with shutdown")

    except ImportError:
        logger.debug("Graph routes not available during shutdown")
    except Exception as e:
        logger.warning(f"Error during graceful shutdown wait: {e}")

    # Stop task queue processor and cleanup job
    await task_manager.stop_queue_processor()
    await task_manager.stop_cleanup_job()

    # Close Redis task store connection
    await task_manager.close_redis()

    # Stop build scheduler gracefully
    scheduler = get_build_scheduler()
    await scheduler.stop()

    # Clean up Redis Pub/Sub connection
    try:
        from api.services.task_pubsub import cleanup_pubsub

        await cleanup_pubsub()
    except ImportError:
        pass

    # Clean up sync state store connection
    try:
        await cleanup_sync_state_store()
    except Exception as e:
        logger.debug(f"Error cleaning up sync state store: {e}")

    # Clean up MCP lifespan
    mcp_lifespan_cm = getattr(app.state, "mcp_lifespan_cm", None)
    if mcp_lifespan_cm:
        try:
            await mcp_lifespan_cm.__aexit__(None, None, None)
            logger.info("MCP lifespan cleaned up")
        except Exception as e:
            logger.debug(f"Error cleaning up MCP lifespan: {e}")

    await cleanup_dependencies()
    logger.info("AtCode API shut down successfully")


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="AtCode API",
        description="High-performance API for code exploration and documentation",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        redirect_slashes=False,
    )

    # Add custom middleware (order matters - last added is first executed)
    # Note: CORS middleware must be added LAST so it executes FIRST
    app.add_middleware(AuthMiddleware)

    # ============================================================================
    # CORS Configuration
    # ============================================================================
    # Pragmatic defaults for Docker/local deployments:
    # 1. localhost / loopback origins on the configured frontend port
    # 2. discovered hostnames and interface IPs for this machine/container
    # 3. ALLOWED_ORIGINS from .env for explicit additions
    # 4. optional regex fallback for private-network IPs on the frontend port
    # 5. optional CORS_ALLOW_ALL_ORIGINS=true escape hatch for debugging/proxies
    frontend_port = os.environ.get("PORT", "3006")
    cors_origins, cors_origin_regex = build_cors_settings(
        frontend_port=frontend_port,
        additional_origins=os.environ.get("ALLOWED_ORIGINS", ""),
    )
    logger.info(f"CORS allowed origins: {cors_origins}")
    if cors_origin_regex:
        logger.info(f"CORS allow_origin_regex: {cors_origin_regex}")

    # Store CORS origins in app state for use in OPTIONS handler
    app.state.cors_origins = cors_origins
    app.state.cors_origin_regex = cors_origin_regex

    # Add CORS middleware LAST so it executes FIRST (handles OPTIONS preflight)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, prefix="/api/health", tags=["Health"])
    app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
    app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
    app.include_router(
        graph_routes.router, prefix="/api/graph", tags=["Knowledge Graph"]
    )
    app.include_router(docs.router, prefix="/api/docs", tags=["Documentation"])
    app.include_router(overview.router, prefix="/api/overview", tags=["Overview"])
    app.include_router(regenerate.router, prefix="/api", tags=["Regeneration"])
    app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
    app.include_router(repos.router, prefix="/api/repos", tags=["Repositories"])
    app.include_router(folders.router, prefix="/api", tags=["Folders"])
    app.include_router(
        config_routes.router, prefix="/api/config", tags=["Configuration"]
    )
    app.include_router(feedback.router, prefix="/api/feedback", tags=["Feedback"])
    app.include_router(
        sync_routes.router, prefix="/api/sync", tags=["Incremental Sync"]
    )
    app.include_router(papers.router, prefix="/api/papers", tags=["Papers"])

    # Mount MCP SSE Server (optional - requires fastmcp)
    # Allows: claude mcp add --transport http atcode http://localhost:8008/mcp
    try:
        from api.routes.mcp_sse import mcp_app

        app.mount("", mcp_app)
        logger.info("MCP endpoint mounted at /mcp (streamable-http transport)")
        logger.info(
            "  Add to Claude Code: claude mcp add --transport http atcode http://localhost:8008/mcp"
        )
    except ImportError as e:
        logger.debug(f"MCP SSE not available (fastmcp not installed): {e}")
    except Exception as e:
        logger.warning(f"Failed to mount MCP SSE endpoint: {e}")

    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint with API information."""
        return {
            "name": "AtCode API",
            "version": VERSION,
            "status": "running",
            "docs": "/docs",
            "health": "/api/health",
            "mcp": "/mcp (SSE transport for Claude Code)",
        }

    # Debug endpoints (only in development)
    if os.getenv("DEBUG", "false").lower() == "true":
        from .routes import debug

        app.include_router(debug.router, prefix="/api/debug", tags=["Debug"])
        logger.info("Debug endpoints enabled")

    # Exception handlers
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Global exception handler."""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "detail": str(exc) if os.getenv("DEBUG") else "An error occurred",
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    return app


# Create application instance
app = create_app()


def get_application() -> FastAPI:
    """Get the FastAPI application instance."""
    return app


if __name__ == "__main__":
    import uvicorn

    config = get_config()

    uvicorn.run(
        "api.main:app",
        host=config.get("api_host", "0.0.0.0"),
        port=config.get("api_port", 8000),
        reload=os.getenv("DEBUG", "false").lower() == "true",
        workers=1,  # Use 1 worker for development, increase for production
    )
