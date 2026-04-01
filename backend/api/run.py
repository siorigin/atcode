#!/usr/bin/env python3
# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
import sys
from pathlib import Path


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run AtCode FastAPI server")

    parser.add_argument(
        "--host",
        default=os.getenv("API_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("API_PORT", "8008")),
        help="Port to bind to (default: 8008)",
    )

    parser.add_argument(
        "--workers", type=int, default=1, help="Number of workers (default: 1)"
    )

    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload for development"
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    return parser.parse_args()


def main():
    """Run the FastAPI server."""
    args = parse_args()

    # Set debug mode
    if args.debug:
        os.environ["DEBUG"] = "true"
        os.environ["LOG_LEVEL"] = "DEBUG"

    # Ensure we're in the backend directory
    backend_dir = Path(__file__).parent.parent
    os.chdir(backend_dir)

    # Add backend to path
    sys.path.insert(0, str(backend_dir))

    print("Starting AtCode FastAPI server...")
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    print(f"  Workers: {args.workers}")
    print(f"  Reload: {args.reload}")
    print(f"  Debug: {args.debug}")
    print()

    try:
        import asyncio

        import uvicorn

        # Windows: asyncio subprocess requires ProactorEventLoop
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        log_level = os.getenv("LOG_LEVEL", "INFO").lower()
        if args.debug:
            log_level = "debug"
        # Disable access log when log level is above INFO (e.g. WARNING, ERROR)
        show_access_log = log_level in ("debug", "info")

        uvicorn.run(
            "api.main:app",
            host=args.host,
            port=args.port,
            workers=args.workers if not args.reload else 1,
            reload=args.reload,
            log_level=log_level,
            access_log=show_access_log,
            timeout_keep_alive=300,  # 5 minutes keep-alive timeout
            timeout_graceful_shutdown=30,  # 30 seconds graceful shutdown
            # CRITICAL: Increase worker health check timeout for long-running tasks
            # Default is 5 seconds, which is too short for graph builds
            # Set to 0 to disable health check timeout entirely
            timeout_worker_healthcheck=0,  # Disable worker health check timeout
        )
    except ImportError:
        print("Error: uvicorn not installed. Install with: pip install uvicorn")
        sys.exit(1)
    except Exception as e:
        print(f"Error starting server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
