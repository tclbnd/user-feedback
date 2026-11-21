"""SCTE 35 splice_info_section."""
import base64
import zlib
from fractions import Fraction

from .bits import Reader

COMMANDS = {0x00: "splice_null", 0x04: "splice_schedule", 0x05: "splice_insert", 0x06: "time_signal",
            0x07: "bandwidth_reservation", 0xFF: "private_command"}
TEXT_UPIDS = {0x09, 0x0F}


class SpliceError(ValueError):
    pass


def crc32(data):
    return zlib.crc32(data[:-4]) == int.from_bytes(data[-4:], "big")


def decode_line(line):
    return base64.b64decode(line.strip(), validate=True)


def seconds(ticks):
    return None if ticks is None else str(Fraction(ticks, 90000))


def splice_time(r, adj):
    r.u(7)
    return r.u(33)


def break_duration(r):
    auto = bool(r.u(1))
    r.u(6)
    return {"auto_return": auto, "duration": seconds(r.u(33))}


def splice_insert(r, adj):
    ev = {"event_id": r.u(32), "cancel": bool(r.u(1))}
    r.u(7)
    out_net, program, has_dur, immediate = r.u(1), r.u(1), r.u(1), r.u(1)
    r.u(4)
    ev.update(out_of_network=bool(out_net), immediate=bool(immediate))
    ev["time"] = seconds(splice_time(r, adj))
    if has_dur:
        ev["break"] = break_duration(r)
    ev["unique_program_id"] = r.u(16)
    ev["avail_num"], ev["avails_expected"] = r.u(8), r.u(8)
    return ev


def upid(kind, raw):
    if kind in TEXT_UPIDS:
        return raw.decode("utf-8")
    return raw.hex()


def segmentation(body, adj):
    r = Reader(body)
    d = {"event_id": r.u(32), "cancel": bool(r.u(1))}
    r.u(7)
    if d["cancel"]:
        return d
    program, has_dur, dnr = r.u(1), r.u(1), r.u(1)
    r.u(5)
    if not program:
        comps = []
        for _ in range(r.u(8)):
            tag = r.u(8)
            r.u(7)
            comps.append({"tag": tag, "pts_offset": seconds(r.u(33))})
        d["components"] = comps
    if has_dur:
        d["duration"] = seconds(r.u(32))
    kind, n = r.u(8), r.u(8)
    d["upid_type"] = kind
    d["upid"] = upid(kind, r.take(n))
    d["type_id"], d["segment_num"], d["segments_expected"] = r.u(8), r.u(8), r.u(8)
    return d


def descriptors(data, adj):
    out, p = [], 0
    while p + 2 <= len(data):
        tag, n = data[p], data[p + 1]
        body = data[p + 2:p + 2 + n]
        p += 2 + n
        ident = body[:4].decode("latin-1")
        if tag == 0x00:
            out.append({"tag": "avail", "provider_avail_id": int.from_bytes(body[4:8], "big")})
        elif tag == 0x02:
            out.append({"tag": "segmentation", **segmentation(body[4:], adj)})
        else:
            out.append({"tag": tag, "identifier": ident, "bytes": n})
    return out


def parse(data):
    if not data or data[0] != 0xFC:
        raise SpliceError("not a splice_info_section")
    r = Reader(data)
    r.u(8)
    r.u(2)
    sap = r.u(2)
    length = r.u(12)
    if 3 + length > len(data):
        raise SpliceError("section truncated")
    data = data[:3 + length]
    crc_ok = crc32(data)
    version = r.u(8)
    encrypted = bool(r.u(1))
    r.u(6)
    adj = r.u(33)
    r.u(8)
    tier = r.u(12)
    cmd_len = r.u(12)
    cmd = r.u(8)
    out = {"protocol_version": version, "sap_type": sap, "tier": tier, "pts_adjustment": seconds(adj),
           "encrypted": encrypted, "command": COMMANDS.get(cmd, "0x%02x" % cmd), "crc_ok": crc_ok}
    start = r.byte
    if cmd == 0x05:
        out["splice_insert"] = splice_insert(r, adj)
    elif cmd == 0x06:
        out["time"] = seconds(splice_time(r, adj))
    elif cmd == 0x00:
        pass
    r.pos = (start + cmd_len) * 8
    dlen = r.u(16)
    out["descriptors"] = descriptors(r.take(dlen), adj)
    return out
