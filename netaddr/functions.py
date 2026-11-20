"""Functions and operators on inet/cidr (PostgreSQL names).

Network-producing functions/operators return (kind, interface) values so
that output() renders them with PostgreSQL's inet/cidr text rules; scalar
results are returned as their psql text (strings) or plain ints.
"""
import ipaddress

from .types import (NetError, abbrev_text, ip_text, output, parse_cidr,
                    parse_inet)

_INT64_MAX = 2 ** 63 - 1
_INT64_MIN = -(2 ** 63)


def _v(x):
    return parse_inet(x) if isinstance(x, str) else x


def _bool(x):
    return "t" if x else "f"


def _mask(width, bits):
    return ((1 << bits) - 1) << (width - bits) if bits else 0


# ---- text / scalar functions ----

def host(a):
    return ip_text(_v(a)[1].ip)


def text(a):
    return output(_v(a))


def abbrev(a):
    return abbrev_text(_v(a))


def masklen(a):
    return str(_v(a)[1].network.prefixlen)


def family(a):
    return str(_v(a)[1].version)


def netmask(a):
    return ip_text(_v(a)[1].netmask)


def hostmask(a):
    return ip_text(_v(a)[1].hostmask)


def inet_same_family(a, b):
    return _bool(_v(a)[1].version == _v(b)[1].version)


# ---- network-producing functions ----

def network(a):
    iface = _v(a)[1]
    # network() is inet-only in PostgreSQL: cidr arguments are cast first.
    return ("inet", ipaddress.ip_interface(iface.network))


def broadcast(a):
    iface = _v(a)[1]
    bcast = iface.network.broadcast_address
    return ("inet", ipaddress.ip_interface((bcast, iface.network.prefixlen)))


def set_masklen(a, bits):
    kind, iface = _v(a)
    if not isinstance(bits, int) or isinstance(bits, bool) or \
            bits < 0 or bits > iface.max_prefixlen:
        raise NetError(f"invalid mask length: {bits}")
    new = ipaddress.ip_interface((iface.ip, bits))
    if kind == "cidr":
        # cidr variant keeps host bits zeroed
        new = ipaddress.ip_interface(new.network)
    return (kind, new)


def inet_merge(a, b):
    ia, ib = _v(a)[1], _v(b)[1]
    if ia.version != ib.version:
        raise NetError("inet_merge: arguments are not of the same family")
    width = ia.max_prefixlen
    x, y = int(ia.ip), int(ib.ip)
    bits = width - (x ^ y).bit_length()      # length of the common prefix
    merged = (x & _mask(width, bits), bits)
    return ("inet", ipaddress.ip_interface(merged))


# ---- containment / overlap operators ----

def contained(a, b):          # a << b  (strict)
    ia, ib = _v(a)[1], _v(b)[1]
    if ia.version != ib.version:
        return "f"
    na, nb = ia.network, ib.network
    return _bool(na != nb and na.subnet_of(nb))


def contained_eq(a, b):       # a <<= b
    ia, ib = _v(a)[1], _v(b)[1]
    if ia.version != ib.version:
        return "f"
    return _bool(ia.network.subnet_of(ib.network))


def contains(a, b):           # a >> b  (strict)
    return contained(b, a)


def overlaps(a, b):           # a && b
    ia, ib = _v(a)[1], _v(b)[1]
    if ia.version != ib.version:
        return "f"
    return _bool(ia.network.overlaps(ib.network))


# ---- arithmetic / bitwise operators ----

def plus(a, n):
    iface = _v(a)[1]
    if not isinstance(n, int) or isinstance(n, bool):
        raise NetError("operator does not exist: inet + non-integer")
    total = int(iface.ip) + n
    if total < 0 or total >= 2 ** iface.max_prefixlen:
        raise NetError("result is out of range")
    return ("inet", ipaddress.ip_interface((total, iface.network.prefixlen)))


def minus(a, b):
    iface = _v(a)[1]
    if isinstance(b, int) and not isinstance(b, bool):
        total = int(iface.ip) - b
        if total < 0 or total >= 2 ** iface.max_prefixlen:
            raise NetError("result is out of range")
        return ("inet", ipaddress.ip_interface((total, iface.network.prefixlen)))
    other = _v(b)[1]
    if iface.version != other.version:
        raise NetError("cannot subtract inet values of different families")
    diff = int(iface.ip) - int(other.ip)
    if not _INT64_MIN <= diff <= _INT64_MAX:
        raise NetError("result is out of range")
    return diff


def bit_not(a):
    iface = _v(a)[1]
    width = iface.max_prefixlen
    n = (~int(iface.ip)) & (2 ** width - 1)
    return ("inet", ipaddress.ip_interface((n, iface.network.prefixlen)))


def bit_and(a, b):
    ia, ib = _v(a)[1], _v(b)[1]
    if ia.version != ib.version:
        raise NetError("arguments are not of same family")
    n = int(ia.ip) & int(ib.ip)
    return ("inet", ipaddress.ip_interface((n, ia.network.prefixlen)))


FUNCTIONS = {
    "inet": parse_inet, "cidr": parse_cidr, "host": host, "text": text,
    "masklen": masklen, "network": network, "broadcast": broadcast,
    "netmask": netmask, "hostmask": hostmask, "set_masklen": set_masklen,
    "abbrev": abbrev, "family": family, "inet_same_family": inet_same_family,
    "inet_merge": inet_merge, "<<": contained, "<<=": contained_eq,
    ">>": contains, "&&": overlaps,
    "+": plus, "-": minus, "~": bit_not, "&": bit_and,
}
