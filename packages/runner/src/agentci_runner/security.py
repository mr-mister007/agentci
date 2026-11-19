from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_HTTP_SCHEMES = frozenset({"http", "https"})


class AgentEndpointError(ValueError):
    """Raised when an agent endpoint is unsafe or malformed."""


def _is_restricted(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    restricted = (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
    if isinstance(address, ipaddress.IPv6Address):
        return restricted or address.is_site_local
    return restricted


def validate_endpoint(
    url: str,
    *,
    allow_private: bool = False,
    allowed_schemes: tuple[str, ...] = ("https",),
) -> str:
    """Validate an agent endpoint without performing any network resolution."""
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise AgentEndpointError(f"invalid endpoint URL: {exc}") from exc

    schemes = set(allowed_schemes) & _HTTP_SCHEMES
    if allow_private:
        schemes.add("http")
    if scheme not in schemes:
        allowed = ", ".join(sorted(schemes)) or "none"
        raise AgentEndpointError(
            f"unsupported endpoint scheme {parsed.scheme!r}; allowed schemes: {allowed}"
        )
    if username is not None or password is not None or "@" in parsed.netloc:
        raise AgentEndpointError("endpoint URL must not contain userinfo")
    if parsed.fragment:
        raise AgentEndpointError("endpoint URL must not contain a fragment")
    if not hostname:
        raise AgentEndpointError("endpoint URL must include a host")

    host = hostname.lower().rstrip(".")
    if not host:
        raise AgentEndpointError("endpoint URL must include a host")
    if host in _METADATA_HOSTS:
        raise AgentEndpointError(f"cloud metadata host {host!r} is not allowed")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not allow_private and _is_restricted(address):
        raise AgentEndpointError(
            "private, loopback, link-local, multicast, reserved or unspecified "
            f"endpoint {host!r} is not allowed"
        )

    default_port = 80 if scheme == "http" else 443
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        netloc += f":{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
