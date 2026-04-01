# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

__all__ = ["run_server", "main"]


def run_server():
    """Lazy import to avoid circular import issues."""
    from .server import run_server as _run_server

    return _run_server()


def main():
    """Lazy import main entry point."""
    from .server import main as _main

    return _main()
