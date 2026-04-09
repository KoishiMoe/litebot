"""
mcping/protocol.py – Address parsing and Minecraft server query helpers.
"""

import re
from typing import Optional


# Matches a bracketed IPv6 address with an optional port: [::1]:25565 or [::1]
_IPV6_BRACKET_RE = re.compile(r"^\[([^\]]+)\](?:[:：](\d+))?$")


def parse_address(raw: str) -> tuple[str, int]:
    """Split a server address string into ``(host, port)``.

    Three cases are handled in order:

    1. **Bracketed IPv6** ``[::addr]`` or ``[::addr]:port`` –
       the only supported way to supply a port alongside an IPv6 address.
    2. **Bare IPv6** (two or more colons, no brackets) –
       the entire string is the host; no port suffix is recognised.
       e.g. ``2001:db8::1`` or ``::1``.
    3. **Hostname / IPv4** (at most one colon) –
       the optional trailing ``:port`` suffix is parsed normally.
       e.g. ``mc.hypixel.net:25565`` or ``127.0.0.1``.

    Returns port ``0`` when absent (callers use the edition default).
    """
    raw = raw.strip()

    # Case 1 – bracketed IPv6
    m = _IPV6_BRACKET_RE.match(raw)
    if m:
        host = m.group(1)
        port_str = m.group(2)
        port = int(port_str) if port_str and 1 <= int(port_str) <= 65535 else 0
        return host, port

    # Case 2 – bare IPv6 (≥2 colons): no port suffix is allowed
    if raw.count(":") >= 2:
        return raw, 0

    # Case 3 – hostname or IPv4 with optional :port
    normalised = raw.replace("：", ":")
    if ":" in normalised:
        host, sep, port_str = normalised.rpartition(":")
        if sep and port_str.isdigit() and 1 <= int(port_str) <= 65535:
            return host, int(port_str)

    return raw, 0


async def autodetect_edition(address: str, port: int) -> Optional[bool]:
    """Try JE then BE.  Returns ``True`` = JE, ``False`` = BE, ``None`` = unreachable.

    When *port* is 0 (unspecified), the Java probe uses :meth:`JavaServer.async_lookup`
    so that DNS SRV records (``_minecraft._tcp.<host>``) are resolved automatically –
    just as the Minecraft client itself does.
    """
    from mcstatus import BedrockServer, JavaServer

    try:
        if port:
            srv = await JavaServer.async_lookup(f"{address}:{port}")
        else:
            # No explicit port → let async_lookup attempt SRV, fall back to 25565
            srv = await JavaServer.async_lookup(address)
        await srv.async_status()
        return True
    except Exception:
        pass
    try:
        srv = BedrockServer(address, port or 19132)
        await srv.async_status()
        return False
    except Exception:
        pass
    return None


async def query_java(address: str, port: int):
    """Return a ``JavaStatusResponse`` or raise.

    When *port* is 0, the address is passed without a port suffix so that
    :meth:`JavaServer.async_lookup` can attempt DNS SRV resolution
    (``_minecraft._tcp.<host>``) before falling back to port 25565.
    Appending ``:25565`` explicitly would cause the library to skip SRV.
    """
    from mcstatus import JavaServer
    lookup_addr = f"{address}:{port}" if port else address
    srv = await JavaServer.async_lookup(lookup_addr)
    return await srv.async_status()


async def query_bedrock(address: str, port: int):
    """Return a ``BedrockStatusResponse`` or raise."""
    from mcstatus import BedrockServer
    srv = BedrockServer(address, port or 19132)
    return await srv.async_status()
