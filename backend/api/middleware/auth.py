# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

# JWT configuration - must match frontend auth.ts
JWT_SECRET = os.environ.get("JWT_SECRET", "atcode-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"

# Optional bearer token security
security = HTTPBearer(auto_error=False)


def _base64url_decode(data: str) -> bytes:
    """Decode base64url encoded string."""
    # Add padding if needed
    padded = data.replace("-", "+").replace("_", "/")
    padding = 4 - len(padded) % 4
    if padding != 4:
        padded += "=" * padding
    return base64.b64decode(padded)


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string."""
    return (
        base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")
    )


def _create_signature(header_payload: str, secret: str) -> str:
    """Create HMAC-SHA256 signature for JWT."""
    signature = hmac.new(
        secret.encode(), header_payload.encode(), hashlib.sha256
    ).digest()
    return _base64url_encode(signature)


def verify_jwt(token: str) -> dict[str, Any] | None:
    """
    Verify and decode a JWT token.

    This implementation matches the frontend auth.ts JWT format:
    - Algorithm: HS256 (HMAC-SHA256)
    - Payload fields: userId, isAnonymous, createdAt, exp

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict if valid, None if invalid/expired
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            logger.debug("JWT: Invalid format (not 3 parts)")
            return None

        header_encoded, payload_encoded, signature = parts

        # Verify signature
        expected_signature = _create_signature(
            f"{header_encoded}.{payload_encoded}", JWT_SECRET
        )
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("JWT: Signature verification failed")
            return None

        # Decode header (optional validation)
        try:
            header = json.loads(_base64url_decode(header_encoded))
            if header.get("alg") != JWT_ALGORITHM:
                logger.warning(f"JWT: Unsupported algorithm {header.get('alg')}")
                return None
        except Exception as e:
            logger.warning(f"JWT: Invalid header: {e}")
            return None

        # Decode payload
        try:
            payload = json.loads(_base64url_decode(payload_encoded))
        except Exception as e:
            logger.warning(f"JWT: Invalid payload: {e}")
            return None

        # Check expiration
        exp = payload.get("exp")
        if exp and exp < time.time():
            logger.debug("JWT: Token expired")
            return None

        return payload

    except Exception as e:
        logger.error(f"JWT verification error: {e}")
        return None


def generate_anonymous_id(request: Request) -> str:
    """
    Generate a stable anonymous user ID based on request info.

    Args:
        request: FastAPI request object

    Returns:
        Anonymous user ID (e.g., "anon-abc123def456")
    """
    # Get client IP
    forwarded = request.headers.get("x-forwarded-for")
    real_ip = request.headers.get("x-real-ip")
    ip = forwarded.split(",")[0] if forwarded else (real_ip or "unknown")

    # Get user agent
    user_agent = request.headers.get("user-agent", "unknown")

    # Create hash for stable anonymous ID
    content = f"{ip}:{user_agent}"
    hash_value = hashlib.sha256(content.encode()).hexdigest()[:12]

    return f"anon-{hash_value}"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """
    Dependency to get the current user ID.

    Priority:
    1. JWT token from Authorization header
    2. User cookie
    3. Anonymous ID based on IP + User-Agent

    Args:
        request: FastAPI request object
        credentials: Optional bearer token

    Returns:
        User identifier string
    """
    # Try JWT token from Authorization header
    if credentials:
        try:
            payload = verify_jwt(credentials.credentials)
            if payload and payload.get("userId"):
                logger.debug(f"Authenticated via JWT: {payload.get('userId')}")
                return payload["userId"]
        except Exception as e:
            logger.warning(f"Invalid token: {e}")

    # Check for user cookie (atcode-auth-token)
    auth_cookie = request.cookies.get("atcode-auth-token")
    if auth_cookie:
        payload = verify_jwt(auth_cookie)
        if payload and payload.get("userId"):
            logger.debug(f"Authenticated via cookie: {payload.get('userId')}")
            return payload["userId"]

    # Legacy cookie support
    user_cookie = request.cookies.get("atcode_user_id")
    if user_cookie:
        return user_cookie

    # Default: Generate anonymous ID
    return generate_anonymous_id(request)


def get_user_session_id(user_id: str, session_id: str) -> str:
    """
    Create a user-scoped session ID.

    Args:
        user_id: User identifier
        session_id: Original session ID (may already contain user_id prefix)

    Returns:
        User-scoped session ID (e.g., "anon-abc123__session-xyz789")
    """
    # Avoid double-prefixing when frontend sends back a session_id
    # that already includes the user_id (e.g., from loaded chat logs)
    if session_id.startswith(f"{user_id}__"):
        return session_id
    return f"{user_id}__{session_id}"


def parse_user_session_id(user_session_id: str) -> tuple[str, str]:
    """
    Parse user-scoped session ID back to components.

    Args:
        user_session_id: User-scoped session ID

    Returns:
        Tuple of (user_id, session_id)

    Raises:
        ValueError: If format is invalid
    """
    if "__" not in user_session_id:
        raise ValueError(f"Invalid user session ID format: {user_session_id}")

    parts = user_session_id.split("__", 1)
    return parts[0], parts[1]


def validate_session_ownership(user_session_id: str, user_id: str) -> bool:
    """
    Validate that a session belongs to a user.

    Args:
        user_session_id: User-scoped session ID
        user_id: User identifier to validate

    Returns:
        True if session belongs to user, False otherwise
    """
    try:
        session_user_id, _ = parse_user_session_id(user_session_id)
        return session_user_id == user_id
    except ValueError:
        return False


class AuthMiddleware:
    """
    Authentication middleware that adds user_id to request state.

    Supports:
    - JWT token from Authorization header (Bearer token)
    - JWT token from cookie (atcode-auth-token)
    - Anonymous ID fallback

    Note: This is a pure ASGI middleware (not BaseHTTPMiddleware) to support
    SSE/streaming responses used by MCP.
    """

    def __init__(self, app):
        self.app = app
        # Paths to skip authentication entirely
        self.skip_paths = {
            "/api/health",
            "/health",
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
        }
        # Path prefixes to skip (SSE/streaming endpoints)
        self.skip_prefixes = ("/mcp", "/messages")

    async def __call__(self, scope, receive, send):
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Create request object for header/cookie access
        request = Request(scope, receive, send)

        # Skip for OPTIONS, certain paths, and MCP/SSE endpoints
        if (
            request.method == "OPTIONS"
            or request.url.path in self.skip_paths
            or request.url.path.startswith(self.skip_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        # Initialize auth state
        user_id = None
        is_authenticated = False

        # Priority 1: Check Authorization header for JWT
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = verify_jwt(token)
            if payload and payload.get("userId"):
                user_id = payload["userId"]
                is_authenticated = not payload.get("isAnonymous", True)
                logger.debug(
                    f"Auth via header: {user_id}, authenticated={is_authenticated}"
                )

        # Priority 2: Check cookie for JWT
        if not user_id:
            auth_cookie = request.cookies.get("atcode-auth-token")
            if auth_cookie:
                payload = verify_jwt(auth_cookie)
                if payload and payload.get("userId"):
                    user_id = payload["userId"]
                    is_authenticated = not payload.get("isAnonymous", True)
                    logger.debug(
                        f"Auth via cookie: {user_id}, authenticated={is_authenticated}"
                    )

        # Priority 3: Legacy cookie support
        if not user_id:
            legacy_cookie = request.cookies.get("atcode_user_id")
            if legacy_cookie:
                user_id = legacy_cookie
                logger.debug(f"Auth via legacy cookie: {user_id}")

        # Priority 4: Generate anonymous ID
        if not user_id:
            user_id = generate_anonymous_id(request)
            logger.debug(f"Using anonymous ID: {user_id}")

        # Add to request state
        scope.setdefault("state", {})
        scope["state"]["user_id"] = user_id
        scope["state"]["is_authenticated"] = is_authenticated

        # Wrap send to add X-User-ID header
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-user-id", user_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
