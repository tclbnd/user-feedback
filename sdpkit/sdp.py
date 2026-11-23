"""SDP session descriptions (RFC 8866) for AES67 / SMPTE ST 2110 streams."""
from fractions import Fraction

WIDTH = {"L16": 2, "L24": 2}
DIRECTIONS = ("sendrecv", "sendonly", "recvonly", "inactive")


class SdpError(ValueError):
    pass


def connection(value):
    parts = value.split()
    if len(parts) != 3 or parts[0] != "IN":
        raise SdpError("bad c= line %r" % value)
    return {"address": parts[2], "ttl": None, "addresses": 1}


def parse(text):
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines or not lines[0].startswith("v="):
        raise SdpError("not an SDP session description")
    session = {"name": None, "connection": None, "direction": "sendrecv", "refclk": None}
    medias = []
    cur = None
    for line in lines:
        if len(line) < 2 or line[1] != "=":
            raise SdpError("bad line %r" % line[:40])
        key, value = line[0], line[2:].strip()
        if key == "m":
            kind, port, proto, *fmts = value.split()
            ports = 1
            cur = {"type": kind, "port": int(port), "ports": int(ports), "protocol": proto, "formats": [int(f) for f in fmts if f.isdigit()],
                   "connection": None, "direction": None, "rtpmap": {}, "fmtp": {}, "ptime": None, "refclk": None,
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
            target = cur if cur is not None else session
            if cur is None:
                continue
            if name in DIRECTIONS:
                target["direction"] = name
            elif name == "ts-refclk":
                target["refclk"] = arg
            elif name == "rtpmap":
                pt, desc = arg.split(None, 1)
                parts = desc.split("/")
                cur["rtpmap"][int(pt)] = (parts[0], int(parts[1]), int(parts[2]) if len(parts) > 2 else None)
            elif name == "fmtp":
                pt, params = arg.split(None, 1)
                cur["fmtp"][int(pt)] = {k.strip(): v.strip() for k, _, v in (p.partition("=") for p in params.split(";") if p.strip())}
            elif name == "ptime":
                cur["ptime"] = int(arg.strip())
            elif name == "mediaclk":
                cur["mediaclk"] = arg
    out = {"session": session["name"], "media": []}
    for m in medias:
        conn = m["connection"]
        payloads = []
        for pt in m["formats"]:
            enc, rate, ch = m["rtpmap"].get(pt) or (None, None, None)
            payloads.append({"pt": pt, "encoding": enc, "clock_rate": rate, "channels": ch, "fmtp": m["fmtp"].get(pt, {})})
        first = payloads[0] if payloads else None
        size = pps = None
        if first and m["ptime"] and first["encoding"] in WIDTH and first["clock_rate"]:
            samples = m["ptime"] * first["clock_rate"] / 1000
            size = int(samples * (first["channels"] or 0) * WIDTH[first["encoding"]])
            pps = str(1000 / m["ptime"])
        out["media"].append({"type": m["type"], "port": m["port"], "ports": m["ports"], "protocol": m["protocol"],
                             "address": conn["address"] if conn else None, "ttl": conn["ttl"] if conn else None,
                             "addresses": conn["addresses"] if conn else None,
                             "direction": m["direction"] or "sendrecv",
                             "refclk": m["refclk"], "mediaclk": m["mediaclk"],
                             "bandwidth_kbps": m["bandwidth"], "payloads": payloads,
                             "ptime": str(m["ptime"]) if m["ptime"] is not None else None,
                             "packet_bytes": size, "packets_per_second": pps})
    return out
