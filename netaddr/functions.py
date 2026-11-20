"""Functions and operators on inet/cidr (PostgreSQL names)."""
import ipaddress

from .types import NetError, output, parse_cidr, parse_inet


def _v(x):
    return parse_inet(x) if isinstance(x, str) else x


def host(a):
    return str(_v(a)[1].ip)


def text(a):
    return output(_v(a))


def masklen(a):
    return str(_v(a)[1].network.prefixlen)


def network(a):
    return str(_v(a)[1].network)


def broadcast(a):
    return str(_v(a)[1].network.broadcast_address)


def netmask(a):
    return str(_v(a)[1].netmask)


def hostmask(a):
    return str(_v(a)[1].hostmask)


def set_masklen(a, bits):
    kind, iface = _v(a)
    return output((kind, ipaddress.ip_interface(f"{iface.ip}/{bits}")))


def abbrev(a):
    return output(_v(a))


def family(a):
    return str(_v(a)[1].version)


def inet_same_family(a, b):
    return "t" if _v(a)[1].version == _v(b)[1].version else "f"


def inet_merge(a, b):
    na, nb = _v(a)[1].network, _v(b)[1].network
    return str(na if na.prefixlen < nb.prefixlen else nb)


def _bool(x):
    return "t" if x else "f"


def contained(a, b):          # a << b
    return _bool(_v(a)[1].network.subnet_of(_v(b)[1].network))


def contained_eq(a, b):       # a <<= b
    return _bool(_v(a)[1].network.subnet_of(_v(b)[1].network))


def contains(a, b):           # a >> b
    return contained(b, a)


def overlaps(a, b):           # a && b
    return _bool(_v(a)[1].network.overlaps(_v(b)[1].network))


def plus(a, n):
    kind, iface = _v(a)
    return str(iface.ip + n)


def minus(a, b):
    kind, iface = _v(a)
    if isinstance(b, int):
        return str(iface.ip - b)
    return str(int(iface.ip) - int(_v(b)[1].ip))


def bit_not(a):
    ip = _v(a)[1].ip
    return str(ipaddress.ip_address(~int(ip) & (2 ** ip.max_prefixlen - 1)))


def bit_and(a, b):
    return str(ipaddress.ip_address(int(_v(a)[1].ip) & int(_v(b)[1].ip)))


FUNCTIONS = {
    "inet": lambda s: parse_inet(s), "cidr": lambda s: parse_cidr(s), "host": host, "text": text,
    "masklen": masklen, "network": network, "broadcast": broadcast, "netmask": netmask, "hostmask": hostmask,
    "set_masklen": set_masklen, "abbrev": abbrev, "family": family, "inet_same_family": inet_same_family,
    "inet_merge": inet_merge, "<<": contained, "<<=": contained_eq, ">>": contains, "&&": overlaps,
    "+": plus, "-": minus, "~": bit_not, "&": bit_and,
}
