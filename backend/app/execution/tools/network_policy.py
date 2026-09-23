"""Shared outbound URL validation for network-capable tools."""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urlparse


MAX_TRANSIENT_ATTEMPTS = 2
MAX_REDIRECTS = 3


def transient_backoff(attempt: int) -> None:
    """Apply a short bounded exponential backoff between transient attempts."""

    time.sleep(min(0.15 * (2**attempt), 0.5))


def _is_private_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _host_resolves_private(host: str, port: int | None) -> bool:
    try:
        addresses = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Let the HTTP client report an unreachable public hostname. It is not
        # safe to infer that an unresolved hostname is private.
        return False
    return any(_is_private_address(item[4][0]) for item in addresses if item[4])


def validate_external_url(raw_url: str) -> tuple[str | None, str | None]:
    """Validate HTTP(S) URLs and reject common SSRF targets.

    Local/private destinations are blocked by default. Development setups that
    intentionally need a local service can opt in explicitly with
    ``AGENTFLOW_ALLOW_PRIVATE_NETWORK=1``.
    """

    candidate = str(raw_url or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, "URL must be an absolute HTTP(S) URL."
    if parsed.username or parsed.password:
        return None, "URLs with embedded credentials are not allowed."
    try:
        port = parsed.port
    except ValueError:
        return None, "URL contains an invalid port."

    host = parsed.hostname.lower().rstrip(".")
    if os.getenv("AGENTFLOW_ALLOW_PRIVATE_NETWORK") != "1":
        if host in {"localhost", "localhost.localdomain", "metadata.google.internal"} or host.endswith(".localhost"):
            return None, "Private and local network targets are blocked."
        if _is_private_address(host) or _host_resolves_private(host, port):
            return None, "Private and local network targets are blocked."

    return candidate, None
