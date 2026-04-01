# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import contextvars
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Context variable for request tracking
request_context = contextvars.ContextVar("request_context", default={})


class StructuredLogger:
    """
    Structured logger with request tracing support.

    Features:
    - Request ID tracking
    - User ID tracking
    - Performance metrics
    - JSON output for production
    """

    def __init__(
        self,
        log_dir: Path | None = None,
        log_level: str = "INFO",
        json_format: bool = False,
    ):
        """
        Initialize structured logger.

        Args:
            log_dir: Directory for log files (None = stdout only)
            log_level: Minimum log level
            json_format: Whether to output JSON format
        """
        self.log_dir = log_dir
        self.log_level = log_level
        self.json_format = json_format

        # Remove default logger
        logger.remove()

        # Add console handler
        if json_format:
            logger.add(
                sys.stderr,
                format=self._json_formatter,
                level=log_level,
                serialize=False,
            )
        else:
            logger.add(
                sys.stderr,
                format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[request_id]}</cyan> | <level>{message}</level>",
                level=log_level,
                filter=self._add_context,
            )

        # Add file handler if log_dir specified
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

            # Main log file
            logger.add(
                log_dir / "atcode.log",
                format=self._json_formatter
                if json_format
                else "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {message}",
                level=log_level,
                rotation="100 MB",
                retention="30 days",
                compression="zip",
                filter=self._add_context,
            )

            # Error log file
            logger.add(
                log_dir / "error.log",
                format=self._json_formatter
                if json_format
                else "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {message}",
                level="ERROR",
                rotation="50 MB",
                retention="90 days",
                compression="zip",
                filter=self._add_context,
            )

    def _add_context(self, record: dict[str, Any]) -> bool:
        """Add context variables to log record.

        Args:
            record: The loguru log record to enrich with context.

        Returns:
            True to indicate the record should be logged.
        """
        ctx = request_context.get()
        record["extra"]["request_id"] = ctx.get("request_id", "no-request")
        record["extra"]["user_id"] = ctx.get("user_id", "unknown")
        record["extra"]["session_id"] = ctx.get("session_id", "")
        return True

    def _json_formatter(self, record: dict[str, Any]) -> str:
        """Format log record as JSON.

        Args:
            record: The loguru log record to format.

        Returns:
            JSON-formatted log string with trailing newline.
        """
        ctx = request_context.get()

        log_entry = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            "request_id": ctx.get("request_id", "no-request"),
            "user_id": ctx.get("user_id", "unknown"),
            "session_id": ctx.get("session_id", ""),
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
        }

        # Add exception info if present
        if record["exception"]:
            log_entry["exception"] = {
                "type": record["exception"].type.__name__,
                "value": str(record["exception"].value),
                "traceback": record["exception"].traceback,
            }

        # Add extra fields
        for key, value in record["extra"].items():
            if key not in ["request_id", "user_id", "session_id"]:
                log_entry[key] = value

        return json.dumps(log_entry) + "\n"

    @staticmethod
    def set_context(
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set logging context for current request.

        Args:
            request_id: Unique request identifier
            user_id: User identifier
            session_id: Session identifier
            **kwargs: Additional context fields
        """
        ctx = request_context.get().copy()

        if request_id:
            ctx["request_id"] = request_id
        if user_id:
            ctx["user_id"] = user_id
        if session_id:
            ctx["session_id"] = session_id

        ctx.update(kwargs)
        request_context.set(ctx)

    @staticmethod
    def clear_context() -> None:
        """Clear logging context."""
        request_context.set({})

    @staticmethod
    def log_performance(
        operation: str, duration_ms: float, metadata: dict[str, Any] | None = None
    ) -> None:
        """Log performance metrics.

        Args:
            operation: Operation name
            duration_ms: Duration in milliseconds
            metadata: Additional metadata
        """
        logger.info(
            f"Performance: {operation}",
            extra={
                "operation": operation,
                "duration_ms": duration_ms,
                "metadata": metadata or {},
            },
        )


# Global logger instance
_logger_instance: StructuredLogger | None = None


def setup_logging(
    log_dir: str | Path | None = None,
    log_level: str = "INFO",
    json_format: bool = False,
) -> StructuredLogger:
    """
    Setup global logging configuration.

    Args:
        log_dir: Directory for log files
        log_level: Minimum log level
        json_format: Whether to use JSON format

    Returns:
        StructuredLogger instance
    """
    global _logger_instance

    if log_dir:
        log_dir = Path(log_dir)

    _logger_instance = StructuredLogger(
        log_dir=log_dir, log_level=log_level, json_format=json_format
    )

    return _logger_instance


def get_logger() -> StructuredLogger:
    """Get the global logger instance."""
    global _logger_instance

    if _logger_instance is None:
        _logger_instance = setup_logging()

    return _logger_instance


# Convenience functions
def set_request_context(**kwargs: Any) -> None:
    """Set request context for logging."""
    get_logger().set_context(**kwargs)


def clear_request_context() -> None:
    """Clear request context."""
    get_logger().clear_context()


def log_performance(operation: str, duration_ms: float, **metadata: Any) -> None:
    """Log performance metrics."""
    get_logger().log_performance(operation, duration_ms, metadata)
