"""SDP session descriptions (RFC 8866) for AES67 / SMPTE ST 2110 streams."""
import ipaddress
from fractions import Fraction

# Audio payload bytes per sample.
WIDTH = {"L16": 2, "L24": 3, "AM824": 3}
DIRECTIONS = ("sendrecv", "sendonly", "recvonly", "inactive")

# RFC 3551 Table 4: static audio payload types that need no rtpmap line.
STATIC_AUDIO = {
    0: ("PCMU", 8000, 1),
    1: ("1016", 8000, 1),
    2: ("G726-32", 8000, 1),
    3: ("GSM", 8000, 1),
    4: ("G723", 8000, 1),
    5: ("DVI4", 8000, 1),
    6: ("DVI4", 8000, 1),
    7: ("LPC", 8000, 1),
    8: ("PCMA", 8000, 1),
    9: ("G722", 8000, 1),
    10: ("L16", 44100, 2),
    11: ("L16", 44100, 1),
    12: ("QCELP", 8000, 1),
    13: ("CN", 8000, 1),
    14: ("MPA", 90000, 1),
    15: ("G728", 8000, 1),
    16: ("DVI4", 11025, 1),
    17: ("DVI4", 11025, 1),
    18: ("G729", 8000, 1),
}

# SMPTE ST 2110-30 channel symbols and the number of audio channels each
# symbol stands for (group symbols expand to their member channels).
CHANNEL_SYMBOLS = {
    "L": 1, "R": 1, "C": 1, "LFE": 1, "Lss": 1, "Rss": 1,
    "Lrs": 1, "Rrs": 1, "Ltf": 1, "Rtf": 1, "Ltb": 1, "Rtb": 1,
    "Lw": 1, "Rw": 1, "Vhl": 1, "Vhc": 1, "Vhr": 1,
    "ST": 2, "M1": 1, "M2": 1, "Lt": 1, "Rt": 1,
    "LFE1": 1, "LFE2": 1, "silent": 1,
    "51": 6, "71": 8, "222": 24,
}


class SdpError(ValueError):
    pass


def _is_cidr_prefix(ip_value, length, max_bits):
    """True when ip/length is a canonical network prefix (host bits zero).

    A suffix such as the "/32" some vendors append to multicast addresses is
    a routing prefix, not the RFC 8866 multicast TTL.  A genuine TTL only
    coincidentally looks like a prefix length, and never leaves the host bits
    of the address clear, so the two cases can be told apart.
    """
    if not 0 <= length <= max_bits:
        return False
    host_bits = max_bits - length
    return (ip_value & ((1 << host_bits) - 1)) == 0


def connection(value):
    parts = value.split()
    if len(parts) != 3 or parts[0] != "IN":
        raise SdpError("bad c= line %r" % value)
    _, addrtype, connaddr = parts
    if addrtype == "IP4":
        return _connection_ip4(connaddr)
    if addrtype == "IP6":
        return _connection_ip6(connaddr)
    raise SdpError("unsupported c= address type %r" % addrtype)


def _connection_ip4(connaddr):
    bits = connaddr.split("/")
    address = bits[0]
    try:
        ip_value = int(ipaddress.IPv4Address(address))
    except ValueError:
        raise SdpError("bad IPv4 connection address %r" % address)
    ttl, addresses = None, 1
    if len(bits) == 2:
        suffix = int(bits[1])
        if _is_cidr_prefix(ip_value, suffix, 32):
            # Spurious "/32"-style routing prefix: no TTL was given.
            pass
        else:
            if not 0 <= suffix <= 255:
                raise SdpError("bad multicast TTL %r" % bits[1])
            ttl = suffix
    elif len(bits) == 3:
        # RFC 8866 IPv4 multicast: address/ttl/number-of-addresses.
        ttl, addresses = int(bits[1]), int(bits[2])
    elif len(bits) > 3:
        raise SdpError("bad c= connection address %r" % connaddr)
    return {"address": address, "ttl": ttl, "addresses": addresses}


def _connection_ip6(connaddr):
    bits = connaddr.split("/")
    address = bits[0]
    try:
        ip_value = int(ipaddress.IPv6Address(address))
    except ValueError:
        raise SdpError("bad IPv6 connection address %r" % address)
    addresses = 1
    if len(bits) == 2:
        suffix = int(bits[1])
        if _is_cidr_prefix(ip_value, suffix, 128):
            # Spurious "/128"-style routing prefix, not an address count.
            pass
        else:
            addresses = suffix
    elif len(bits) > 2:
        raise SdpError("bad c= connection address %r" % connaddr)
    return {"address": address, "ttl": None, "addresses": addresses}


def _source_filter(arg):
    """First source address of an RFC 4570 incl filter, else None."""
    fields = arg.split()
    # filter-mode nettype addrtype dest-address src-list...
    if len(fields) >= 5 and fields[0] == "incl":
        return fields[4]
    return None


def _ptp(refclk):
    """Break an AES67 ptp= reference clock into its parts."""
    if not refclk or not refclk.startswith("ptp="):
        return None
    fields = refclk[len("ptp="):].split(":")
    version = fields[0]
    if len(fields) == 2 and fields[1] == "traceable":
        return {"version": version, "grandmaster": None,
                "domain": None, "traceable": True}
    if len(fields) >= 2:
        domain = int(fields[2]) if len(fields) >= 3 and fields[2] else 0
        return {"version": version, "grandmaster": fields[1].upper(),
                "domain": domain, "traceable": False}
    return None


def _rtp_offset(mediaclk):
    if mediaclk and mediaclk.startswith("direct="):
        value = mediaclk[len("direct="):]
        return int(value) if value else None
    return None


def _ordered_channels(payload):
    """Channels described by an ST 2110-30 SMPTE2110 channel-order fmtp."""
    if not payload:
        return None
    order = payload.get("fmtp", {}).get("channel-order")
    if not order:
        return None
    convention, dot, rest = order.partition(".")
    if not dot or convention != "SMPTE2110":
        return None
    rest = rest.strip()
    if len(rest) < 2 or rest[0] != "(" or rest[-1] != ")":
        return None
    symbols = [s.strip() for s in rest[1:-1].split(",") if s.strip()]
    if not symbols:
        return None
    total = 0
    for symbol in symbols:
        if symbol not in CHANNEL_SYMBOLS:
            return None
        total += CHANNEL_SYMBOLS[symbol]
    return total


def parse(text):
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines or not lines[0].startswith("v="):
        raise SdpError("not an SDP session description")
    session = {"name": None, "connection": None, "direction": None,
               "refclk": None, "source": None}
    medias = []
    cur = None
    for line in lines:
        if len(line) < 2 or line[1] != "=":
            raise SdpError("bad line %r" % line[:40])
        key, value = line[0], line[2:].strip()
        if key == "m":
            kind, portfield, proto, *fmts = value.split()
            port, _, count = portfield.partition("/")
            cur = {"type": kind, "port": int(port), "ports": int(count) if count else 1,
                   "protocol": proto, "formats": [int(f) for f in fmts if f.isdigit()],
                   "connection": None, "direction": None, "rtpmap": {}, "fmtp": {},
                   "ptime": None, "refclk": None, "source": None,
                   "mediaclk": None, "bandwidth": None}
            medias.append(cur)
        elif key == "s" and cur is None:
            session["name"] = value or None
        elif key == "c":
            (cur or session)["connection"] = connection(value)
        elif key == "b" and cur is not None and value.startswith("AS:"):
            cur["bandwidth"] = int(value[3:])
        elif key == "a":
            name, _, arg = value.partition(":")
            if cur is None:
                # Session-level attributes: the ones a media description may
                # inherit when it does not give its own value.
                if name in DIRECTIONS:
                    session["direction"] = name
                elif name == "ts-refclk":
                    session["refclk"] = arg
                elif name == "source-filter":
                    session["source"] = _source_filter(arg)
                continue
            if name in DIRECTIONS:
                cur["direction"] = name
            elif name == "ts-refclk":
                cur["refclk"] = arg
            elif name == "source-filter":
                cur["source"] = _source_filter(arg)
            elif name == "rtpmap":
                pt, desc = arg.split(None, 1)
                parts = desc.split("/")
                channels = int(parts[2]) if len(parts) > 2 else (1 if cur["type"] == "audio" else None)
                cur["rtpmap"][int(pt)] = (parts[0], int(parts[1]), channels)
            elif name == "fmtp":
                pt, params = arg.split(None, 1)
                cur["fmtp"][int(pt)] = {k.strip(): v.strip()
                                        for k, _, v in (p.partition("=")
                                                        for p in params.split(";") if p.strip())}
            elif name == "ptime":
                # RFC 8866 allows fractional values ("0.125").
                cur["ptime"] = Fraction(arg.strip())
            elif name == "mediaclk":
                cur["mediaclk"] = arg
    out = {"session": session["name"], "media": []}
    for m in medias:
        conn = m["connection"] or session["connection"]
        refclk = m["refclk"] if m["refclk"] is not None else session["refclk"]
        source = m["source"] if m["source"] is not None else session["source"]
        payloads = []
        for pt in m["formats"]:
            if pt in m["rtpmap"]:
                enc, rate, channels = m["rtpmap"][pt]
            elif m["type"] == "audio" and pt in STATIC_AUDIO:
                enc, rate, channels = STATIC_AUDIO[pt]
            else:
                enc = rate = channels = None
            if channels is None and m["type"] == "audio":
                channels = 1
            payloads.append({"pt": pt, "encoding": enc, "clock_rate": rate,
                             "channels": channels, "fmtp": m["fmtp"].get(pt, {})})
        first = payloads[0] if payloads else None
        size = pps = None
        if first and m["ptime"] is not None and first["encoding"] in WIDTH and first["clock_rate"]:
            samples = m["ptime"] * first["clock_rate"] / 1000
            if samples.denominator != 1:
                raise SdpError("ptime %s does not give a whole number of samples" % m["ptime"])
            size = samples.numerator * (first["channels"] or 1) * WIDTH[first["encoding"]]
            pps = str(Fraction(1000, 1) / m["ptime"])
        out["media"].append(
            {"type": m["type"], "port": m["port"], "ports": m["ports"], "protocol": m["protocol"],
             "address": conn["address"] if conn else None,
             "ttl": conn["ttl"] if conn else None,
             "addresses": conn["addresses"] if conn else None,
             "direction": m["direction"] or session["direction"] or "sendrecv",
             "refclk": refclk, "mediaclk": m["mediaclk"],
             "bandwidth_kbps": m["bandwidth"], "payloads": payloads,
             "ptime": str(m["ptime"]) if m["ptime"] is not None else None,
             "packet_bytes": size, "packets_per_second": pps,
             "ptp": _ptp(refclk), "rtp_offset": _rtp_offset(m["mediaclk"]),
             "source": source, "ordered_channels": _ordered_channels(first)})
    return out
