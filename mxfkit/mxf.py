"""MXF (SMPTE ST 377-1) files: KLV, partitions, header metadata, index tables, essence."""
from fractions import Fraction

PREFIX = bytes.fromhex("060e2b34")
RIP = bytes.fromhex("060e2b3402050101" "0d01020101110100")
PRIMER = bytes.fromhex("060e2b3402050101" "0d01020101050100")
INDEX = bytes.fromhex("060e2b3402530101" "0d01020101100100")
SET_TYPES = {0x1400: "timecode", 0x3600: "material", 0x3700: "source", 0x3B00: "track", 0x0F00: "sequence",
             0x2800: "cdci", 0x2900: "rgba", 0x4200: "sound", 0x4800: "sound", 0x4700: "sound", 0x4400: "multiple"}
PICTURE_DEF = bytes.fromhex("060e2b34040101010103020201000000")
OPS = {(1, 1): "OP1a", (1, 2): "OP1b", (1, 3): "OP1c", (2, 1): "OP2a", (2, 2): "OP2b", (2, 3): "OP2c",
       (3, 1): "OP3a", (3, 2): "OP3b", (3, 3): "OP3c"}
ITEMS = {0x15: "picture", 0x05: "picture", 0x06: "sound", 0x04: "system", 0x14: "system"}

# properties by their SMPTE ST 377-1 local tag
UL = {k: bytes.fromhex(v) for k, v in {
    0x3C0A: "060e2b34010101010101150200000000", 0x4403: "060e2b34010101020601010405000000",
    0x4803: "060e2b34010101020601010402040000", 0x4B01: "060e2b34010101020530040500000000",
    0x0202: "060e2b34010101020702020101020000", 0x0201: "060e2b34010101020407010000000000",
    0x3001: "060e2b34010101010406010100000000", 0x3002: "060e2b34010101020406010200000000",
    0x3202: "060e2b34010101010401050201010000", 0x3203: "060e2b34010101010401050202020000",
    0x320C: "060e2b34010101010401030102010400", 0x3301: "060e2b34010101020401050300000000",
    0x3D03: "060e2b34010101050402030101010000", 0x3D07: "060e2b34010101050402010104000000",
    0x3D01: "060e2b34010101040402030304000000", 0x3F01: "060e2b34010101040601010406000000",
    0x3F0B: "060e2b34010101050530040600000000", 0x3F0C: "060e2b34010101050702010301040000",
    0x3F0D: "060e2b34010101050702020101020000", 0x3F05: "060e2b34010101040406020100000000",
    0x3F06: "060e2b34010101040106020100000000", 0x3F07: "060e2b34010101040106010300000000",
    0x1001: "060e2b34010101020601010409000000", 0x1501: "060e2b34010101020702010301050000",
    0x1502: "060e2b34010101010404010102060000", 0x1503: "060e2b34010101010404010105000000",
}.items()}


class MxfError(ValueError):
    pass


def ber(data, pos):
    b = data[pos]
    if b < 0x80:
        return b, pos + 1
    n = b & 0x7F
    if not 1 <= n <= 4 or pos + 1 + n > len(data):
        raise MxfError("bad BER length at %d" % pos)
    return int.from_bytes(data[pos + 1:pos + 1 + n], "big"), pos + 1 + n


def klvs(data, start):
    pos = start
    while pos + 17 <= len(data):
        key = data[pos:pos + 16]
        if key[:4] != PREFIX:
            raise MxfError("not a KLV key at %d" % pos)
        length, v = ber(data, pos + 16)
        if v + length > len(data):
            raise MxfError("KLV at %d runs past the end of the file" % pos)
        yield key, v, v + length
        pos = v + length


def is_partition(key):
    return key[:13] == RIP[:13] and key[13] in (2, 3, 4)


def _u(raw):
    return int.from_bytes(raw, "big")


def _rational(raw):
    return Fraction(_u(raw[:4]))


def _refs(raw):
    count, size = _u(raw[:4]), _u(raw[4:8])
    return [raw[8 + i * size:8 + (i + 1) * size] for i in range(count)]


def _local_set(data, b, e, primer):
    props, p = {}, b
    while p + 4 <= e:
        tag, n = _u(data[p:p + 2]), _u(data[p + 2:p + 4])
        if tag in UL:
            props[tag] = data[p + 4:p + 4 + n]
        p += 4 + n
    return props


def _primer(data, b, e):
    count, size = _u(data[b:b + 4]), _u(data[b + 4:b + 8])
    return {_u(data[b + 8 + i * size:b + 10 + i * size]): data[b + 10 + i * size:b + 8 + (i + 1) * size]
            for i in range(count)}


def _metadata(sets):
    by_uid = {s[0x3C0A]: (kind, s) for kind, s in sets if 0x3C0A in s}
    out = {"edit_rate": None, "duration_edit_units": None, "video": [], "audio": [], "start_timecode": None}
    for kind, track in sets:
        if kind != "track":
            continue
        seq = by_uid.get(track.get(0x4803), (None, {}))[1]
        for ref in _refs(seq.get(0x1001, bytes(8))):
            k, tc = by_uid.get(ref, (None, {}))
            if k == "timecode" and out["start_timecode"] is None:
                frames, base = _u(tc.get(0x1501, bytes(8))), _u(tc.get(0x1502, b"\0\x1e"))
                drop = bool(_u(tc.get(0x1503, b"\0")))
                s = frames // base
                out["start_timecode"] = "%02d:%02d:%02d%s%02d" % (s // 3600 % 24, s // 60 % 60, s % 60, ";" if drop else ":", frames % base)
        if seq.get(0x0201) == PICTURE_DEF:
            out["edit_rate"] = _rational(track[0x4B01])
            out["duration_edit_units"] = _u(seq[0x0202]) if 0x0202 in seq else None
            break
    subs = set()
    for kind, s in sets:
        if kind == "multiple":
            subs.update(_refs(s.get(0x3F01, bytes(8))))
    for kind, s in sets:
        if kind == "multiple":
            continue
        if kind in ("cdci", "rgba"):
            layout = _u(s.get(0x320C, b"\0"))
            h = _u(s.get(0x3202, b"\0\0\0\0"))
            out["video"].append({"width": _u(s.get(0x3203, b"\0\0\0\0")),
                                 "height": h,
                                 "interlaced": layout in (1, 4), "bit_depth": _u(s[0x3301]) if 0x3301 in s else None,
                                 "sample_rate": str(_rational(s[0x3001])) if 0x3001 in s else None})
        elif kind == "sound":
            out["audio"].append({"sample_rate": str(_u(s[0x3D03])) if 0x3D03 in s else None,
                                 "channels": _u(s[0x3D07]) if 0x3D07 in s else None,
                                 "bits": _u(s[0x3D01]) if 0x3D01 in s else None})
    return out


def parse(data):
    start = 0
    if not is_partition(data[:16]):
        raise MxfError("no header partition")
    parts, essence, segments = [], {}, {}
    essence_bytes, fill_bytes = {}, 0
    rip = False
    cur = None
    for key, b, e in klvs(data, start):
        if is_partition(key):
            status = key[14]
            op = data[b + 64:b + 80]
            cur = {"kind": {2: "header", 3: "body", 4: "footer"}[key[13]], "closed": status in (2, 4),
                   "complete": status in (3, 4), "body_sid": _u(data[b + 60:b + 64]),
                   "index_sid": _u(data[b + 48:b + 52]), "_op": op, "_primer": {}, "_sets": []}
            parts.append(cur)
        elif key == PRIMER:
            cur["_primer"] = _primer(data, b, e)
        elif key[4] == 0x02 and key[5] == 0x53 and key[8:13] == bytes.fromhex("0d01010101") and cur is not None:
            kind = SET_TYPES.get(_u(key[13:15]))
            if kind:
                cur["_sets"].append((kind, _local_set(data, b, e, cur["_primer"])))
        elif key == INDEX:
            s = _local_set(data, b, e, cur["_primer"] if cur else {})
            segments[len(segments)] = s
        elif key[:12] == bytes.fromhex("060e2b34010201010d010301"):
            kind = ITEMS.get(key[12], "other")
            essence[kind] = essence.get(kind, 0) + 1
            essence_bytes[kind] = essence_bytes.get(kind, 0) + (e - b) + 20
        elif key == bytes.fromhex("060e2b34010101020301021001000000"):
            fill_bytes += e - b
        elif key == RIP:
            rip = True
    if not parts:
        raise MxfError("no partitions")
    best = next((p for p in parts if p["_sets"]), None)
    meta = _metadata(best["_sets"]) if best else _metadata([])
    op = parts[0]["_op"]
    pattern = OPS.get((op[12], op[13]), "unknown")
    rate, dur = meta.pop("edit_rate"), meta["duration_edit_units"]
    cbe = {_u(s[0x3F05]) for s in segments.values() if _u(s.get(0x3F05, b"\0" * 4))}
    return {"run_in": start, "operational_pattern": pattern,
            "partitions": [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts], "rip": rip,
            "edit_rate": str(rate) if rate else None, "duration_edit_units": dur,
            "duration": str(dur / rate) if rate and dur is not None else None,
            "start_timecode": meta["start_timecode"], "video": meta["video"], "audio": meta["audio"], "essence": dict(sorted(essence.items())),
            "essence_bytes": dict(sorted(essence_bytes.items())), "fill_bytes": fill_bytes,
            "index": {"segments": len(segments),
                      "edit_units": sum(int.from_bytes(s.get(0x3F0D, bytes(8)), "big", signed=True) for s in segments.values()),
                      "constant_bytes": cbe.pop() if len(cbe) == 1 else None}}
