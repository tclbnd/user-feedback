"""inet and cidr values of the rule engine (PostgreSQL semantics).

A value is (kind, interface) where kind is "inet" or "cidr" and interface
is an ipaddress.IPv4Interface / IPv6Interface.  Text input and output
follow the PostgreSQL types of the same name.
"""
import ipaddress


class NetError(Exception):
    pass


def parse_inet(text):
    try:
        return ("inet", ipaddress.ip_interface(text.strip()))
    except ValueError as exc:
        raise NetError(f'invalid input syntax for type inet: "{text}"') from exc


def parse_cidr(text):
    try:
        net = ipaddress.ip_network(text.strip(), strict=False)
    except ValueError as exc:
        raise NetError(f'invalid input syntax for type cidr: "{text}"') from exc
    return ("cidr", ipaddress.ip_interface(str(net)))


def output(value):
    kind, iface = value
    return str(iface)
