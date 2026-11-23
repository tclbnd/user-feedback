"""MXF (SMPTE ST 377-1) files: KLV, partitions, header metadata, index tables, essence."""
from fractions import Fraction

PREFIX = bytes.fromhex("060e2b34")
RIP = bytes.fromhex("060e2b3402050101" "0d01020101110100")
PRIMER = bytes.fromhex("060e2b3402050101" "0d01020101050100")
INDEX = bytes.fromhex("060e2b3402530101" "0d01020101100100")
FILL_TAIL = bytes.fromhex("0301021001000000")
ESSENCE_PREFIX = bytes.fromhex("060e2b34010201010d010301")
SET_TYPES = {0x1400: "timecode", 0x3600: "material", 0x3700: "source", 0x3B00: "track", 0x0F00: "sequence",
             0x2800: "cdci", 0x2900: "rgba", 0x4200: "sound", 0x4800: "sound", 0x4700: "sound", 0x4400: "multiple"}
PICTURE_DEF = bytes.fromhex("060e2b34040101010103020201000000")
TIMECODE_DEF = bytes.fromhex("060e2b34040101010103020101000000")
OP_ATOM = bytes.fromhex("060e2b34040101020d010201")
OPS = {(1, 1): "OP1a", (1, 2): "OP1b", (1, 3): "OP1c", (2, 1): "OP2a", (2, 2): "OP2b", (2, 3): "OP2c",
       (3, 1): "OP3a", (3, 2): "OP3b", (3, 3): "OP3c"}
# Essence element keys (ST 377-1 table A.1) carry the item type in byte 12;
# the low numbers are the legacy keys, 0x14..0x18 the current universal labels.
ITEMS = {0x15: "picture", 0x05: "picture", 0x16: "sound", 0x06: "sound", 0x17: "data", 0x07: "data",
         0x18: "compound", 0x08: "compound", 0x14: "system", 0x04: "system"}

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


def _partition_fields(body):
    """Return (body_sid, index_sid, op_ul) from a partition pack value.

    ST 377-1 partitions put Body SID at byte 48 and the Operational Pattern
    UL at byte 52 (there is no Index SID field). Some writers emit a legacy
    88-byte layout with an Index SID inserted before Body SID; the OP UL is
    located by its label prefix so both layouts parse correctly.
    """
    op_at = body.find(OP_ATOM[:7])  # 060e2b34 0401 01 -> OP1x/OP2x/OP3x
    if op_at < 0:
        op_at = body.find(OP_ATOM)   # OP-Atom: 060e2b34 0401 02 ...
    if op_at == 52:
        return _u(body[48:52]), 0, body[52:68]
    if op_at == 64:
        # Legacy 88-byte layout: Index SID (4) + zero pad (4) + Body SID (4)
        # + zero pad (4) + Operational Pattern UL (16) + essence batch.
        return _u(body[56:60]), _u(body[48:52]), body[64:80]
    raise MxfError("no operational pattern in partition pack")


def _u(raw):
    return int.from_bytes(raw, "big")


def _i(raw):
    return int.from_bytes(raw, "big", signed=True)


def _bool(raw, default=False):
    return bool(raw[0]) if raw else default


def _rational(raw):
    # ST 377-1 Rational: 32-bit numerator followed by 32-bit denominator
    return Fraction(_u(raw[:4]), _u(raw[4:8]))


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


def _timecode(sets, by_uid):
    """Start timecode of the material package's timecode track, HH:MM:SS:FF."""
    for kind, mp in sets:
        if kind != "material":
            continue
        for trk_ref in _refs(mp.get(0x4403, bytes(8))):
            trk = by_uid.get(trk_ref, (None, {}))[1]
            seq = by_uid.get(trk.get(0x4803), (None, {}))[1]
            if seq.get(0x0201) != TIMECODE_DEF:
                continue
            for ref in _refs(seq.get(0x1001, bytes(8))):
                k, tc = by_uid.get(ref, (None, {}))
                if k != "timecode":
                    continue
                # Start Timecode (0x1501) is an 8-byte struct: 2-byte count of
                # frames since midnight, then a 6-byte packed timecode; the
                # rounded timecode rate (0x1502) is a Rational, defaulting to 30.
                stc = tc.get(0x1501)
                if not stc:
                    continue
                frames = _u(stc[:2])
                base = _rational(tc[0x1502]) if 0x1502 in tc else Fraction(30, 1)
                drop = _bool(tc.get(0x1503, b"\0"))
                s = frames // base
                return "%02d:%02d:%02d%s%02d" % (s // 3600 % 24, s // 60 % 60, s % 60,
                                                  ";" if drop else ":", frames % base)
        break
    return None


def _metadata(sets):
    by_uid = {s[0x3C0A]: (kind, s) for kind, s in sets if 0x3C0A in s}
    out = {"edit_rate": None, "duration_edit_units": None, "video": [], "audio": [],
           "start_timecode": _timecode(sets, by_uid)}
    material = next((s for kind, s in sets if kind == "material"), None)
    # Picture track of the material package: edit rate and duration in edit units.
    if material is not None:
        for trk_ref in _refs(material.get(0x4403, bytes(8))):
            trk = by_uid.get(trk_ref, (None, {}))[1]
            seq = by_uid.get(trk.get(0x4803), (None, {}))[1]
            if seq.get(0x0201) != PICTURE_DEF:
                continue
            if 0x4B01 in trk:
                out["edit_rate"] = _rational(trk[0x4B01])
            if 0x0202 in seq:
                out["duration_edit_units"] = _i(seq[0x0202])
            break
    # Descriptors: the MultipleDescriptor itself only groups the picture and
    # sound descriptors and is never reported on its own.
    for kind, s in sets:
        if kind == "multiple":
            continue
        if kind in ("cdci", "rgba"):
            layout = _u(s.get(0x320C, b"\0"))
            interlaced = layout in (1, 4)
            h = _u(s.get(0x3202, b"\0\0\0\0"))
            if interlaced:
                # For separate fields / one field per edit unit the stored
                # height is the field height; the frame has twice that many lines.
                h *= 2
            out["video"].append({"width": _u(s.get(0x3203, b"\0\0\0\0")),
                                 "height": h, "interlaced": interlaced,
                                 "bit_depth": _u(s[0x3301]) if 0x3301 in s else None,
                                 "sample_rate": str(_rational(s[0x3001])) if 0x3001 in s else None})
            # Fallback when the material package picture duration is unknown:
            # the descriptor's Container Duration (0x3002).
            if out["duration_edit_units"] is None and 0x3002 in s:
                out["duration_edit_units"] = _i(s[0x3002])
        elif kind == "sound":
            out["audio"].append({"sample_rate": str(_rational(s[0x3D03])) if 0x3D03 in s else None,
                                 "channels": _u(s[0x3D07]) if 0x3D07 in s else None,
                                 "bits": _u(s[0x3D01]) if 0x3D01 in s else None})
    return out


def parse(data):
    # Everything before the first header partition pack is run-in data. It can
    # contain arbitrary KLV (e.g. system metadata written by cameras), so walk
    # KLV boundaries instead of assuming the header pack starts at byte 0.
    start = 0
    found = False
    while start + 17 <= len(data):
        if data[start:start + 4] != PREFIX:
            start += 1
            continue
        if is_partition(data[start:start + 16]) and data[start + 13] == 2:
            found = True
            break
        try:
            _, nxt = ber(data, start + 16)
        except MxfError:
            start += 1
            continue
        start = nxt
    if not found:
        raise MxfError("no header partition")
    parts, essence, segments = [], {}, {}
    essence_bytes, fill_bytes = {}, 0
    rip = False
    cur = None
    active_primer = {}  # the primer remains in force until the next one
    for key, b, e in klvs(data, start):
        if is_partition(key):
            kind = {2: "header", 3: "body", 4: "footer"}[key[13]]
            status = key[14]
            # ST 377-1 partition packs carry Body SID and the Operational
            # Pattern UL; the Index SID is a property of the index table
            # segments, so a standard pack reports zero until one is seen.
            body_sid, pack_index_sid, op = _partition_fields(data[b:e])
            # Standard packs have no Index SID (pack_index_sid == 0); the
            # legacy layout may carry one, which keeps its value until an
            # index table segment in this partition supplies its own 0x3F06.
            cur = {"kind": kind, "closed": status in (2, 4), "complete": status in (3, 4),
                   "body_sid": body_sid, "index_sid": pack_index_sid, "_op": op, "_sets": []}
            parts.append(cur)
        elif key == PRIMER:
            # A primer stays in force until another primer pack is seen.
            active_primer = _primer(data, b, e)
        elif key[4] == 0x02 and key[5] == 0x53 and key[8:13] == bytes.fromhex("0d01010101") and cur is not None:
            kind = SET_TYPES.get(_u(key[13:15]))
            if kind:
                cur["_sets"].append((kind, _local_set(data, b, e, active_primer)))
        elif key == INDEX:
            s = _local_set(data, b, e, active_primer)
            # Distinct index table segments only: repeated copies of the same
            # segment (e.g. header and footer) share one Instance UID.
            uid = s.get(0x3C0A) or data[b:e]
            if uid not in segments:
                segments[uid] = s
                if cur is not None and 0x3F06 in s:
                    cur["index_sid"] = _u(s[0x3F06])
        elif key[:12] == ESSENCE_PREFIX:
            kind = ITEMS.get(key[12], "other")
            essence[kind] = essence.get(kind, 0) + 1
            essence_bytes[kind] = essence_bytes.get(kind, 0) + (e - b)
        elif key[:4] == PREFIX and key[4:7] == bytes.fromhex("010101") and key[8:16] == FILL_TAIL:
            # KLV fill: the version byte (key[7]) varies between writers.
            fill_bytes += e - b
        elif key == RIP:
            rip = True
    if not parts:
        raise MxfError("no partitions")

    # Best header metadata copy: prefer a closed and complete partition.
    def score(p):
        return (1 if p["closed"] else 0) + (1 if p["complete"] else 0)

    best = max((p for p in parts if p["_sets"]), key=score, default=None)
    meta = _metadata(best["_sets"]) if best else _metadata([])
    op = parts[0]["_op"]
    if op[:12] == OP_ATOM:
        pattern = "OP-Atom"
    else:
        # Operational Pattern UL ...02 01 NN MM 09 00: item NN at byte 12,
        # type MM at byte 13.
        pattern = OPS.get((op[12], op[13]), "unknown")
    rate, dur = meta.pop("edit_rate"), meta["duration_edit_units"]
    # Constant Edit Unit Bytes is meaningful only when every distinct segment
    # reports the same non-zero value.
    cbe = {_u(s[0x3F05]) for s in segments.values() if _u(s.get(0x3F05, b"\0" * 4))}
    return {"run_in": start, "operational_pattern": pattern,
            "partitions": [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts], "rip": rip,
            "edit_rate": str(rate) if rate else None, "duration_edit_units": dur,
            "duration": str(dur / rate) if rate and dur is not None else None,
            "start_timecode": meta["start_timecode"], "video": meta["video"], "audio": meta["audio"],
            "essence": dict(sorted(essence.items())),
            "essence_bytes": dict(sorted(essence_bytes.items())), "fill_bytes": fill_bytes,
            "index": {"segments": len(segments),
                      "edit_units": sum(_i(s[0x3F0D]) for s in segments.values() if 0x3F0D in s),
                      "constant_bytes": cbe.pop() if len(cbe) == 1 else None}}
