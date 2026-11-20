"""inet and cidr values of the rule engine (PostgreSQL semantics).

A value is (kind, interface) where kind is "inet" or "cidr" and interface
is an ipaddress.IPv4Interface / IPv6Interface.  Text input and output
follow the PostgreSQL types of the same name, including every input
PostgreSQL rejects and the exact text PostgreSQL prints.
"""
import ipaddress

_DIGITS = set("0123456789")


class NetError(Exception):
    pass


def _fail(kind, text):
    raise NetError(f'invalid input syntax for type {kind}: "{text}"')


def _ascii_uint(s, max_value, kind, raw):
    if not s or any(ch not in _DIGITS for ch in s):
        _fail(kind, raw)
    value = int(s)
    if value > max_value:
        _fail(kind, raw)
    return value


def _parse_v4(text, kind):
    raw = text
    if "/" in text:
        addr_part, _, mask_part = text.partition("/")
        if "/" in mask_part or not mask_part:
            _fail(kind, raw)
    else:
        addr_part, mask_part = text, None

    groups = addr_part.split(".")
    # PostgreSQL requires exactly four decimal octets: "10.20" is rejected.
    if len(groups) != 4 or any(g == "" for g in groups):
        _fail(kind, raw)
    octets = [_ascii_uint(g, 255, kind, raw) for g in groups]
    addr_int = 0
    for octet in octets:
        addr_int = (addr_int << 8) | octet

    if mask_part is None:
        bits = 32
    elif "." in mask_part:
        mgroups = mask_part.split(".")
        if len(mgroups) != 4 or any(g == "" for g in mgroups):
            _fail(kind, raw)
        mask_int = 0
        for octet in (_ascii_uint(g, 255, kind, raw) for g in mgroups):
            mask_int = (mask_int << 8) | octet
        ones = bin(mask_int).count("1")
        # PostgreSQL only accepts contiguous (1*0*) netmasks.
        if mask_int != (0xFFFFFFFF << (32 - ones)) & 0xFFFFFFFF:
            _fail(kind, raw)
        bits = ones
    else:
        bits = _ascii_uint(mask_part, 32, kind, raw)

    try:
        iface = ipaddress.IPv4Interface((addr_int, bits))
    except ValueError:
        _fail(kind, raw)
    return iface


def _parse_v6(text, kind):
    raw = text
    if "%" in text or "[" in text or "]" in text:
        _fail(kind, raw)
    if "/" in text:
        addr_part, _, mask_part = text.partition("/")
        if "/" in mask_part or not mask_part:
            _fail(kind, raw)
    else:
        addr_part, mask_part = text, None

    try:
        addr = ipaddress.IPv6Address(addr_part)
    except ValueError:
        _fail(kind, raw)
    bits = 128 if mask_part is None else _ascii_uint(mask_part, 128, kind, raw)
    try:
        return ipaddress.IPv6Interface((int(addr), bits))
    except ValueError:
        _fail(kind, raw)


def _parse(text, kind):
    if not isinstance(text, str):
        raise NetError(f'invalid input syntax for type {kind}: "{text}"')
    iface = _parse_v6(text, kind) if ":" in text else _parse_v4(text, kind)
    if kind == "cidr":
        host_bits = int(iface.ip) & int(iface.hostmask)
        if host_bits:
            raise NetError(f'invalid cidr value: "{text}"')
    return (kind, iface)


def parse_inet(text):
    return _parse(text, "inet")


def parse_cidr(text):
    return _parse(text, "cidr")


def ip_text(addr):
    """PostgreSQL inet_ntop-style text of a bare address."""
    if isinstance(addr, ipaddress.IPv6Address):
        n = int(addr)
        # inet_ntop renders IPv4-mapped addresses with a dotted quad.
        if n >> 32 == 0xFFFF:
            lo = n & 0xFFFFFFFF
            return "::ffff:%d.%d.%d.%d" % (
                (lo >> 24) & 0xFF, (lo >> 16) & 0xFF, (lo >> 8) & 0xFF, lo & 0xFF)
    return str(addr)


def output(value):
    kind, iface = value
    text = ip_text(iface.ip)
    # cidr always carries a mask; inet hides the family-maximum mask.
    if kind == "cidr" or iface.network.prefixlen != iface.max_prefixlen:
        text += f"/{iface.network.prefixlen}"
    return text


def abbrev_text(value):
    """PostgreSQL abbrev(): text, with v4 cidr trailing zero octets removed."""
    kind, iface = value
    bits = iface.network.prefixlen
    if kind == "inet" or iface.version == 6:
        return output(value)
    if bits == 0:
        return "0/0"
    # keep exactly ceil(bits/8) octets (10.1.0.0/16 -> "10.1/16",
    # 192.168.0.0/24 -> "192.168.0/24")
    keep = bits // 8 + (1 if bits % 8 else 0)
    octets = [int(iface.ip) >> (8 * (3 - i)) & 0xFF for i in range(4)]
    return ".".join(str(o) for o in octets[:keep]) + f"/{bits}"