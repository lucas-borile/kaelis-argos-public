from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
BLOCKED_EXACT_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}
ALLOWED_URL_SCHEMES = {"http", "https", "rtsp"}


class UnsafeNetworkTargetError(ValueError):
    pass


def is_production_environment() -> bool:
    value = os.getenv("ARGOS_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    return value in {"prod", "production"}


def allow_private_camera_networks() -> bool:
    configured = os.getenv("ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return not is_production_environment()


def validate_safe_url(url: str, *, context: str = "url", allowed_schemes: set[str] | None = None) -> str:
    value = str(url or "").strip()
    if not value:
        raise UnsafeNetworkTargetError(f"{context} is empty")
    parsed = urlparse(value)
    schemes = allowed_schemes or ALLOWED_URL_SCHEMES
    if parsed.scheme.lower() not in schemes:
        raise UnsafeNetworkTargetError(f"{context} scheme is not allowed")
    if not parsed.hostname:
        raise UnsafeNetworkTargetError(f"{context} host is required")
    validate_safe_host(parsed.hostname, context=context)
    return value


def validate_safe_host(host: str, *, context: str = "host") -> str:
    value = str(host or "").strip()
    if not value:
        raise UnsafeNetworkTargetError(f"{context} is empty")
    for address in resolve_host_addresses(value):
        validate_safe_ip(address, context=context)
    return value


def resolve_host_addresses(host: str) -> list[IPAddress]:
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeNetworkTargetError(f"could not resolve host {host!r}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeNetworkTargetError(f"could not resolve host {host!r}")
    return [ipaddress.ip_address(address) for address in addresses]


def validate_safe_ip(address: IPAddress, *, context: str = "host") -> None:
    if address in BLOCKED_EXACT_IPS:
        raise UnsafeNetworkTargetError(f"{context} points to blocked metadata address")
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise UnsafeNetworkTargetError(f"{context} points to a blocked local address")
    if address.is_private and not allow_private_camera_networks():
        raise UnsafeNetworkTargetError(f"{context} points to a private network address")
