"""SDP session descriptions (RFC 8866) for AES67 / SMPTE ST 2110 streams."""
import ipaddress
from fractions import Fraction

# Bytes per sample, per channel, of the audio payload encodings used by AES67
# and ST 2110-30.  L16 and L24 are packed sample widths; AM824 is the 32-bit
# AES3 word.
WIDTH = {"L16": 2, "L24": 3, "AM824": 4}
DIRECTIONS = ("sendrecv", "sendonly", "recvonly", "inactive")

# RFC 3551 static payload types: encoding, clock rate, channels (None where
# the payload is not audio).  These need no rtpmap.
STATIC_PAYLOADS = {
    0: ("PCMU", 8000, 1),
    1: ("1016", 8000, 1),
    2: ("G721", 8000, 1),
    3: ("GSM", 8000, 1),
    4: ("G723", 8000, 1),
    5: ("DVI4", 8000, 1),
    6: ("DVI4", 16000, 1),
    7: ("LPC", 8000, 1),
    8: ("PCMA", 8000, 1),
    9: ("G722", 8000, 1),
    10: ("L16", 44100, 2),
    11: ("L16", 44100, 1),
    12: ("QCELP", 8000, 1),
    13: ("CN", 8000, 1),
    14: ("MPA", 90000, None),
    15: ("G728", 8000, 1),
    16: ("DVI4", 8000, 1),
    17: ("DVI4", 8000, 1),
    18: ("G729", 8000, 1),
    25: ("CelB", 90000, None),
    26: ("JPEG", 90000, None),
    31: ("H261", 90000, None),
    32: ("MPV", 90000, None),
    33: ("MP2T", 90000, None),
    34: ("H263", 90000, None),
}


class SdpError(ValueError):
    pass


def is_multicast(address):
    try:
        return ipaddress.ip_address(address).is_multicast
    except ValueError:
        return False


def connection(value):
    """Parse a c= line.

    IPv4 multicast is ``addr/ttl[/count]``; IPv6 multicast is ``addr[/count]``.
    A suffix on a unicast address is a prefix length some devices leave in the
    connection address; it is neither a TTL nor a count.
    """
    parts = value.split()
    if len(parts) != 3 or parts[0] != "IN" or parts[1] not in ("IP4", "IP6"):
        raise SdpError("bad c= line %r" % value)
    addrtype, routed = parts[1], parts[2]
    fields = routed.split("/")
    address, extra = fields[0], fields[1:]
    ttl = addresses = None
    try:
        if is_multicast(address):
            if addrtype == "IP4":
                if extra:
                    ttl = int(extra[0])
                    if not 0 <= ttl <= 255:
                        raise SdpError("bad ttl in c= line %r" % value)
                if len(extra) > 1:
                    addresses = int(extra[1])
            elif extra:
                addresses = int(extra[0])
        if addresses is not None and addresses < 1:
            raise SdpError("bad address count in c= line %r" % value)
    except ValueError:
        raise SdpError("bad c= line %r" % value)
    return {"address": address, "ttl": ttl, "addresses": addresses or 1}


def media_port(value):
    """Split the ``port[/count]`` field of an m= line."""
    port, sep, count = value.partition("/")
    try:
        port = int(port)
        count = int(count) if sep else 1
    except ValueError:
        raise SdpError("bad m= port %r" % value)
    if count < 1:
        raise SdpError("bad m= port count %r" % value)
    return port, count


def ptime(value):
    try:
        value = Fraction(value.strip())
    except (ValueError, ZeroDivisionError):
        raise SdpError("bad ptime %r" % value)
    if value <= 0:
        raise SdpError("bad ptime %r" % value)
    return value


def parse_ptp(refclk):
    """Return the details of a ``ptp=`` reference clock, else None."""
    if not refclk:
        return None
    clock = next((t for t in refclk.split() if t.startswith("ptp=")), None)
    if clock is None:
        return None
    parts = clock[4:].split(":")
    version, rest = parts[0], parts[1:]
    if rest and rest[0].lower() == "traceable":
        return {"version": version, "grandmaster": None, "domain": None, "traceable": True}
    grandmaster = rest[0].upper() if rest and rest[0] else None
    domain = 0
    if len(rest) > 1 and rest[1]:
        try:
            domain = int(rest[1])
        except ValueError:
            raise SdpError("bad ts-refclk %r" % refclk)
    return {"version": version, "grandmaster": grandmaster, "domain": domain, "traceable": False}


def rtp_offset(mediaclk):
    """The value of direct= in a mediaclk attribute, else None."""
    if not mediaclk:
        return None
    for token in mediaclk.split():
        key, _, value = token.partition("=")
        if key == "direct":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def source_filter(arg):
    """First source address of an incl source-filter, else None."""
    parts = arg.split()
    if len(parts) >= 5 and parts[0] == "incl":
        return parts[4]
    return None


def payload(pt, rtpmap, fmtp, kind):
    enc, rate, channels = rtpmap.get(pt) or STATIC_PAYLOADS.get(pt) or (None, None, None)
    if channels is None and kind == "audio":
        channels = 1
    return {"pt": pt, "encoding": enc, "clock_rate": rate, "channels": channels,
            "fmtp": fmtp.get(pt, {})}


def parse(text):
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines or not lines[0].startswith("v="):
        raise SdpError("not an SDP session description")
    session = {"name": None, "connection": None, "direction": None, "refclk": None,
               "source": None}
    medias = []
    cur = None
    for line in lines:
        if len(line) < 2 or line[1] != "=":
            raise SdpError("bad line %r" % line[:40])
        key, value = line[0], line[2:].strip()
        if key == "m":
            fields = value.split()
            if len(fields) < 3:
                raise SdpError("bad m= line %r" % value)
            kind, port, proto = fields[0], fields[1], fields[2]
            port, ports = media_port(port)
            cur = {"type": kind, "port": port, "ports": ports, "protocol": proto,
                   "formats": [int(f) for f in fields[3:] if f.isdigit()],
                   "connection": None, "direction": None, "rtpmap": {}, "fmtp": {},
                   "ptime": None, "refclk": None, "mediaclk": None, "bandwidth": None,
                   "source": None}
            medias.append(cur)
        elif key == "s" and cur is None:
            session["name"] = value or None
        elif key == "c":
            (cur if cur is not None else session)["connection"] = connection(value)
        elif key == "b" and cur is not None and value.startswith("AS:"):
            try:
                cur["bandwidth"] = int(value[3:])
            except ValueError:
                raise SdpError("bad b= line %r" % value)
        elif key == "a":
            target = cur if cur is not None else session
            name, _, arg = value.partition(":")
            if name in DIRECTIONS:
                target["direction"] = name
            elif name == "ts-refclk":
                target["refclk"] = arg.strip() or None
            elif name == "source-filter":
                source = source_filter(arg)
                if source is not None:
                    target["source"] = source
            elif cur is not None:
                if name == "rtpmap":
                    try:
                        pt, desc = arg.split(None, 1)
                        fields = desc.split("/")
                        enc, rate = fields[0].strip(), int(fields[1])
                        channels = int(fields[2]) if len(fields) > 2 else None
                        cur["rtpmap"][int(pt)] = (enc, rate, channels)
                    except (ValueError, IndexError):
                        raise SdpError("bad rtpmap %r" % arg)
                elif name == "fmtp":
                    try:
                        pt, params = arg.split(None, 1)
                        cur["fmtp"][int(pt)] = {k.strip(): v.strip() for k, _, v in
                                                (p.partition("=") for p in params.split(";") if p.strip())}
                    except ValueError:
                        raise SdpError("bad fmtp %r" % arg)
                elif name == "ptime":
                    cur["ptime"] = ptime(arg)
                elif name == "mediaclk":
                    cur["mediaclk"] = arg.strip() or None
    out = {"session": session["name"], "media": []}
    for m in medias:
        conn = m["connection"] or session["connection"]
        refclk = m["refclk"] or session["refclk"]
        payloads = [payload(pt, m["rtpmap"], m["fmtp"], m["type"]) for pt in m["formats"]]
        first = payloads[0] if payloads else None
        size = packets_per_second = None
        if m["ptime"] is not None:
            packets_per_second = str(Fraction(1000) / m["ptime"])
        if first and m["ptime"] is not None and first["clock_rate"] and first["encoding"] in WIDTH:
            samples = m["ptime"] * first["clock_rate"] * (first["channels"] or 0) / 1000
            exact = samples * WIDTH[first["encoding"]]
            if exact.denominator == 1:
                size = int(exact)
        out["media"].append({"type": m["type"], "port": m["port"], "ports": m["ports"],
                             "protocol": m["protocol"],
                             "address": conn["address"] if conn else None,
                             "ttl": conn["ttl"] if conn else None,
                             "addresses": conn["addresses"] if conn else None,
                             "direction": m["direction"] or session["direction"] or "sendrecv",
                             "refclk": refclk, "mediaclk": m["mediaclk"],
                             "bandwidth_kbps": m["bandwidth"], "payloads": payloads,
                             "ptime": str(m["ptime"]) if m["ptime"] is not None else None,
                             "packet_bytes": size, "packets_per_second": packets_per_second,
                             "ptp": parse_ptp(refclk),
                             "rtp_offset": rtp_offset(m["mediaclk"]),
                             "source": m["source"] if m["source"] is not None
                             else session["source"]})
    return out
