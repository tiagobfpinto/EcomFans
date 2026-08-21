"""Global guard on outbound HTTP connections.

Several features fetch URLs that a user supplied — the scraper, the competitor
image proxy, the image harvester. Each of those already validates the URL before
fetching it, but a resolve-then-fetch check leaves a DNS-rebinding window: the
attacker's nameserver answers with a public address for the validation lookup
and with 127.0.0.1 (or 169.254.169.254) for the lookup ``requests`` makes a
moment later.

This module closes that window for *every* outbound request in the process by
wrapping urllib3's socket factory. We resolve the hostname once, reject the host
if any answer points somewhere private, and then connect to the literal address
we validated, so there is no second lookup to poison. TLS is unaffected: urllib3
passes the original hostname as ``server_hostname``, so SNI and certificate
verification still happen against the real name.

Call :func:`install` once at process start (the Flask app and the worker both
do). Set ``ALLOW_PRIVATE_OUTBOUND=true`` to disable it when you are developing
against a service on localhost.
"""

from __future__ import annotations

import ipaddress
import os
import socket

import urllib3.util.connection as _urllib3_connection


class BlockedAddressError(OSError):
    """Raised when a connection targets a non-public address.

    Subclasses OSError so ``requests`` reports it as a normal connection
    failure instead of escaping as an unexpected exception type.
    """


# Ranges that are not covered by the ipaddress ``is_private`` family but that we
# never want to reach: cloud instance metadata and the NAT64 well-known prefix.
_EXTRA_BLOCKED = (
    ipaddress.ip_network("169.254.0.0/16"),      # link-local incl. 169.254.169.254
    ipaddress.ip_network("100.64.0.0/10"),       # carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),        # IETF protocol assignments
    ipaddress.ip_network("64:ff9b::/96"),        # NAT64
    ipaddress.ip_network("fd00::/8"),            # unique local addresses
)

_installed = False


def _allow_private() -> bool:
    raw = (os.getenv("ALLOW_PRIVATE_OUTBOUND") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when *ip* must never be dialled from a user-influenced request."""
    # An IPv4 address tunnelled through IPv6 (::ffff:127.0.0.1) has to be judged
    # on the address it actually reaches.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        ip = sixtofour

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return any(ip in network for network in _EXTRA_BLOCKED)


def _parse_ip(host: str):
    candidate = (host or "").strip().strip("[]")
    # Strip a scope id such as fe80::1%eth0 before parsing.
    candidate = candidate.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def resolve_public_addresses(host: str, port: int) -> list[str]:
    """Resolve *host* and return its addresses, or raise if any is not public.

    Failing closed on a *single* bad answer is deliberate: a host that resolves
    to both a public and a private address is exactly the split-horizon trick
    this guard exists to stop.
    """
    literal = _parse_ip(host)
    if literal is not None:
        if is_blocked_ip(literal):
            raise BlockedAddressError(
                f"Refusing to connect to non-public address {literal}."
            )
        return [str(literal)]

    if (host or "").strip().lower().rstrip(".") in {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }:
        raise BlockedAddressError("Refusing to connect to localhost.")

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedAddressError(f"Could not resolve {host!r}.") from exc

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw = sockaddr[0]
        parsed = _parse_ip(raw)
        if parsed is None:
            raise BlockedAddressError(f"Could not parse resolved address {raw!r}.")
        if is_blocked_ip(parsed):
            raise BlockedAddressError(
                f"{host!r} resolves to non-public address {parsed}."
            )
        addresses.append(str(parsed))

    if not addresses:
        raise BlockedAddressError(f"Could not resolve {host!r}.")
    return addresses


def install() -> None:
    """Wrap urllib3's connection factory. Safe to call more than once."""
    global _installed
    if _installed:
        return
    _installed = True

    original_create_connection = _urllib3_connection.create_connection

    def guarded_create_connection(address, *args, **kwargs):
        if _allow_private():
            return original_create_connection(address, *args, **kwargs)

        host, port = address[0], address[1]
        addresses = resolve_public_addresses(host, port)

        last_error: OSError | None = None
        for candidate in addresses:
            try:
                # Dial the address we just validated so no second, poisonable
                # lookup happens between the check and the connect.
                return original_create_connection((candidate, port), *args, **kwargs)
            except OSError as exc:
                last_error = exc
        raise last_error or BlockedAddressError(f"Could not connect to {host!r}.")

    _urllib3_connection.create_connection = guarded_create_connection
