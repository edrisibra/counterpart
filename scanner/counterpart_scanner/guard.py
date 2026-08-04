"""URL admission control for the hosted scanner.

The scanner fetches a URL supplied by an anonymous visitor, which is a textbook
server-side request forgery surface. Left open, a visitor can use the scanner as a
proxy into the host's own network: cloud instance metadata, internal admin panels,
databases bound to a private interface.

Nothing here is clever. It resolves the hostname, then refuses to proceed unless
every address it resolves to is publicly routable.

Known limits, stated rather than hidden:

* Time-of-check to time-of-use. We validate the addresses DNS returns, then hand the
  URL to httpx, which resolves again. A name that answers with a public address and
  then a private one on the second query defeats this. Pinning the connection to a
  validated address needs a custom transport; :func:`resolved_addresses` returns what
  it saw so a caller can do that later.
* Redirects are not followed by the check engine, so a 302 into a private address goes
  nowhere. If that ever changes, each hop needs the same admission check.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

# Hostnames that resolve to a public address but mean something private to the host.
_BLOCKED_NAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

# Cloud instance metadata services. 169.254.0.0/16 is link-local so it is already
# refused, but naming these makes the intent legible to the next reader.
_BLOCKED_ADDRESSES = frozenset(
    {
        "169.254.169.254",  # AWS, Azure, GCP, DigitalOcean
        "fd00:ec2::254",  # AWS IMDS over IPv6
        "100.100.100.200",  # Alibaba
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Ports that are never an A2A agent and often are something we should not touch.
_BLOCKED_PORTS = frozenset(
    {
        22,  # ssh
        23,  # telnet
        25,  # smtp
        445,  # smb
        1433,  # mssql
        3306,  # mysql
        3389,  # rdp
        5432,  # postgres
        6379,  # redis
        9200,  # elasticsearch
        11211,  # memcached
        27017,  # mongodb
    }
)

MAX_URL_LENGTH = 2048


class UrlRejected(Exception):
    """The submitted URL may not be fetched. The message is safe to show a visitor."""


@dataclass(frozen=True)
class Target:
    """A URL that passed admission control."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _reject_address(raw: str) -> str | None:
    """Return a reason this address may not be fetched, or None if it is allowed."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return f"{raw} is not an IP address"

    # An IPv4 address wrapped in IPv6 (::ffff:127.0.0.1) must be judged as IPv4.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        ip = sixtofour

    if str(ip) in _BLOCKED_ADDRESSES:
        return "cloud instance metadata addresses are not scannable"
    if ip.is_loopback:
        return "loopback addresses are not scannable"
    if ip.is_link_local:
        return "link-local addresses are not scannable"
    if ip.is_private:
        return "private addresses are not scannable"
    if ip.is_reserved:
        return "reserved addresses are not scannable"
    if ip.is_multicast:
        return "multicast addresses are not scannable"
    if ip.is_unspecified:
        return "the unspecified address is not scannable"
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "carrier-grade NAT addresses are not scannable"
    return None


def resolved_addresses(host: str, port: int) -> tuple[str, ...]:
    """Every address ``host`` resolves to, or raise :class:`UrlRejected`."""
    try:
        info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UrlRejected(f"could not resolve {host}") from None
    found = tuple(dict.fromkeys(str(item[4][0]) for item in info))
    if not found:
        raise UrlRejected(f"could not resolve {host}")
    return found


def admit(raw_url: str) -> Target:
    """Validate a visitor-supplied URL, or raise :class:`UrlRejected`.

    Every message raised is safe to render back to the visitor: it says what rule was
    broken and never leaks whether an internal host happens to exist.
    """
    url = (raw_url or "").strip()
    if not url:
        raise UrlRejected("enter a URL")
    if len(url) > MAX_URL_LENGTH:
        raise UrlRejected("that URL is too long")
    if "://" not in url:
        url = "https://" + url

    parts = urlparse(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UrlRejected("only http and https URLs can be scanned")
    if not parts.hostname:
        raise UrlRejected("that URL has no host in it")
    if parts.username or parts.password:
        raise UrlRejected("URLs with embedded credentials are not accepted")

    host = parts.hostname.rstrip(".").lower()
    if host in _BLOCKED_NAMES or host == "localhost" or host.endswith(".localhost"):
        raise UrlRejected(f"{host} is not scannable")
    if host.endswith((".internal", ".local", ".localdomain")):
        raise UrlRejected("internal hostnames are not scannable")

    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UrlRejected("that URL has an invalid port") from None
    if port in _BLOCKED_PORTS:
        raise UrlRejected(f"port {port} is not scannable")

    addresses = resolved_addresses(host, port)
    for address in addresses:
        reason = _reject_address(address)
        if reason is not None:
            raise UrlRejected(reason)

    # Rebuild from the parsed pieces so a crafted URL cannot smuggle anything past.
    netloc = f"{host}:{port}" if parts.port else host
    clean = f"{parts.scheme}://{netloc}{parts.path or ''}".rstrip("/")
    return Target(url=clean, host=host, port=port, addresses=addresses)
