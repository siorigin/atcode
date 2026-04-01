# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Helpers for building pragmatic CORS settings for local and Docker deployments."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit


DEFAULT_FRONTEND_PORT = "3006"
_FALSEY = {"0", "false", "no", "off"}


def _normalize_origin(origin: str) -> str | None:
    """Normalize an origin string to ``scheme://host[:port]`` form."""
    value = origin.strip().rstrip("/")
    if not value:
        return None

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None

    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _origin_from_host(host: str, port: str, scheme: str = "http") -> str:
    """Build an origin from a host and port, adding IPv6 brackets when needed."""
    normalized_host = host.strip().lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    return f"{scheme}://{normalized_host}:{port}"


def _is_enabled(value: str | None, *, default: bool = True) -> bool:
    """Parse a truthy env flag."""
    if value is None:
        return default
    return value.strip().lower() not in _FALSEY


def parse_additional_origins(raw_origins: str) -> set[str]:
    """Parse ``ALLOWED_ORIGINS`` as a comma-separated origin list."""
    origins: set[str] = set()
    for candidate in raw_origins.split(","):
        normalized = _normalize_origin(candidate)
        if normalized:
            origins.add(normalized)
    return origins


def discover_local_hostnames() -> set[str]:
    """Return hostnames that commonly map back to the current machine."""
    hostnames = {"localhost"}
    for candidate in (
        os.environ.get("HOSTNAME"),
        socket.gethostname(),
        socket.getfqdn(),
    ):
        if not candidate:
            continue
        value = candidate.strip().lower().rstrip(".")
        if value:
            hostnames.add(value)
    return hostnames


def discover_local_ip_addresses() -> set[str]:
    """Best-effort discovery of IPs that can route back to this machine."""
    addresses: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        candidate = value.split("%", 1)[0].strip()
        if not candidate:
            return
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return
        if ip.is_unspecified:
            return
        addresses.add(str(ip))

    for hostname in discover_local_hostnames():
        try:
            infos = socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            continue
        for info in infos:
            add(info[4][0])

    probes: tuple[tuple[int, tuple[object, ...]], ...] = (
        (socket.AF_INET, ("192.0.2.1", 80)),
        (socket.AF_INET6, ("2001:db8::1", 80, 0, 0)),
    )
    for family, target in probes:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect(target)
                add(sock.getsockname()[0])
        except OSError:
            continue

    return addresses


def build_private_network_origin_regex(frontend_port: str) -> str:
    """Allow common local/private-network browser origins on the frontend port."""
    escaped_port = re.escape(frontend_port)
    ipv4_private = (
        r"(?:127(?:\.\d{1,3}){3}|"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2})"
    )
    ipv6_local = r"\[(?:::1|f[cd][0-9a-f:]+|fe80:[0-9a-f:]+)\]"
    return rf"^https?://(?:localhost|0\.0\.0\.0|{ipv4_private}|{ipv6_local}):{escaped_port}$"


def build_allow_all_origin_regex() -> str:
    """Allow any HTTP(S) browser origin.

    This is intended for local debugging scenarios such as SSH port-forwarding
    or ad-hoc reverse proxies where enumerating frontend origins is brittle.
    """
    return r"^https?://.+$"


def build_cors_settings(
    frontend_port: str | None = None,
    additional_origins: str | None = None,
    allow_private_networks: bool | None = None,
    allow_all_origins: bool | None = None,
) -> tuple[list[str], str | None]:
    """Build exact origins plus an optional regex fallback for local/private access."""
    port = (frontend_port or DEFAULT_FRONTEND_PORT).strip() or DEFAULT_FRONTEND_PORT
    if allow_all_origins is None:
        allow_all_origins = _is_enabled(
            os.environ.get("CORS_ALLOW_ALL_ORIGINS"),
            default=False,
        )

    if allow_all_origins:
        origins = (
            sorted(parse_additional_origins(additional_origins))
            if additional_origins
            else []
        )
        return origins, build_allow_all_origin_regex()

    origins = {
        _origin_from_host("localhost", port),
        _origin_from_host("127.0.0.1", port),
        _origin_from_host("0.0.0.0", port),
        _origin_from_host("::1", port),
    }

    for hostname in discover_local_hostnames():
        origins.add(_origin_from_host(hostname, port))

    for ip in discover_local_ip_addresses():
        origins.add(_origin_from_host(ip, port))

    if additional_origins:
        origins.update(parse_additional_origins(additional_origins))

    if allow_private_networks is None:
        allow_private_networks = _is_enabled(
            os.environ.get("CORS_ALLOW_PRIVATE_NETWORKS"),
            default=True,
        )

    origin_regex = (
        build_private_network_origin_regex(port) if allow_private_networks else None
    )
    return sorted(origins), origin_regex
