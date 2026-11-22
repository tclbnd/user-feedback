"""MXF (SMPTE ST 377-1) files: KLV, partitions, header metadata, index tables, essence."""
from fractions import Fraction

PREFIX = bytes.fromhex("060e2b34")
RIP = bytes.fromhex("060e2b3402050101" "0d01020101110100")
PRIMER = bytes.fromhex("060e2b3402050101" "0d01020101050100")
INDEX = bytes.fromhex("060e2b3402530101" "0d01020101100100")
# Generic container set keys (ST 377-1: 06.0e.2b.34.02.53.01.01.0d.01.01.01 + set type word).
SET_TYPES = {0x3600: "material", 0x3700: "source", 0x3B00: "track", 0x0F00: "sequence",
             0x2800: "cdci", 0x2900: "rgba", 0x4200: "sound", 0x4800: "sound", 0x4700: "sound",
             0x4400: "multiple"}
PICTURE_DEF = bytes.fromhex("060e2b34040101010103020201000000")
OPS = {(1, 1): "OP1a", (1, 2): "OP1b", (1, 3): "OP1c", (2, 1): "OP2a", (2, 2): "OP2b", (2, 3): "OP2c",
       (3, 1): "OP3a", (3, 2): "OP3b", (3, 3): "OP3c", (0x10, 0): "OP-Atom"}
# Essence element keys (ST 377-1 table A.1): ...0d.01.03.01 + item type byte.
# 0x05/0x15 picture, 0x06/0x16 sound, 0x07/0x17 data, 0x08/0x18 compound, 0x04/0x14 system.
ITEMS = {0x04: "system", 0x14: "system", 0x05: "picture", 0x15: "picture", 0x06: "sound",
         0x16: "sound", 0x07: "data", 0x17: "data", 0x08: "compound", 0x18: "compound"}
# Frame Layout (ST 377-1): 1 = separate fields, 4 = mixed fields; both carry one field per
# stored picture, so Stored Height is a field height and the frame height is twice that.
INTERLACED_LAYOUTS = (1, 4)
# Length of an unknown (open-ended) value, e.g. a -1 duration.
UNKNOWN_LEN = (1 << 64) - 1

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


def _is_header_partition(key):
    # The header pack's key fixes the layout for the whole file.
    # shifted: ...01 02 status 00 (kind 2 in byte 13);
    # standard ST 377-1: ...01 04 02 status (version 4 in byte 13, kind 2 in byte 14).
    return key[:12] == RIP[:12] and (
        key[13] == 2 or (key[13] == 4 and key[14] == 2))


def is_partition(key, shifted=True):
    if key[:12] != RIP[:12]:
        return False
    if shifted:
        # Kind in byte 13, status in byte 14, trailing zero.
        return key[13] in (2, 3, 4) and key[15] == 0
    # ST 377-1: version 0x04 in byte 13, kind in byte 14, status (0..3) in byte 15.
    return key[13] == 4 and key[14] in (2, 3, 4) and key[15] in (0, 1, 2, 3)


def partition_kind(key, shifted=True):
    return key[13] if shifted else key[14]


def partition_status(key, shifted=True):
    return key[14] if shifted else key[15]


def _u(raw):
    return int.from_bytes(raw, "big")


def _rational(raw):
    # An MXF Rational is a pair of 32-bit integers: numerator over denominator.
    return Fraction(_u(raw[:4]), _u(raw[4:8]))


def _refs(raw):
    if len(raw) < 8:
        return []
    count, size = _u(raw[:4]), _u(raw[4:8])
    return [raw[8 + i * size:8 + (i + 1) * size] for i in range(count)]


def _local_set(data, b, e, primer):
    # Local tags may be the ST 377-1 static tags or dynamic tags. The primer maps a
    # dynamic local tag to the property's UL, which in turn identifies the property.
    static_by_ul = {ul: tag for tag, ul in UL.items()}
    props, p = {}, b
    while p + 4 <= e:
        tag, n = _u(data[p:p + 2]), _u(data[p + 2:p + 4])
        if tag in UL:
            props[tag] = data[p + 4:p + 4 + n]
        else:
            mapped = static_by_ul.get(primer.get(tag))
            if mapped is not None:
                props[mapped] = data[p + 4:p + 4 + n]
        p += 4 + n
    return props


def _primer(data, b, e):
    count, size = _u(data[b:b + 4]), _u(data[b + 4:b + 8])
    return {_u(data[b + 8 + i * size:b + 10 + i * size]): data[b + 10 + i * size:b + 8 + (i + 1) * size]
            for i in range(count)}


def _metadata(sets):
    by_uid = {s[0x3C0A]: (kind, s) for kind, s in sets if 0x3C0A in s}
    out = {"edit_rate": None, "duration_edit_units": None, "video": [], "audio": []}

    # Duration comes from the material package's picture track: the material track's
    # EditRate together with its source sequence's Duration.
    for kind, package in sets:
        if kind != "material":
            continue
        for ref in _refs(package.get(0x4403, b"")):
            track = by_uid.get(ref)
            if not track or track[0] != "track":
                continue
            seq = by_uid.get(track[1].get(0x4803, b""))
            if not seq or seq[0] != "sequence" or seq[1].get(0x0201) != PICTURE_DEF:
                continue
            out["edit_rate"] = _rational(track[1][0x4B01]) if 0x4B01 in track[1] else None
            length = _u(seq[1][0x0202]) if 0x0202 in seq[1] else UNKNOWN_LEN
            out["duration_edit_units"] = None if length == UNKNOWN_LEN else length
            break
        break

    # A Multiple Descriptor only groups its sub-descriptors (the actual CDCI/RGBA/Sound
    # descriptors); it is not itself a picture or sound descriptor.
    for kind, s in sets:
        if kind == "multiple":
            continue
        if kind in ("cdci", "rgba"):
            layout = _u(s.get(0x320C, b"\0"))
            height = _u(s.get(0x3202, b"\0\0\0\0"))
            if layout in INTERLACED_LAYOUTS:
                # Separate/mixed fields store one field per picture; the frame has two.
                height *= 2
            out["video"].append({"width": _u(s.get(0x3203, b"\0\0\0\0")),
                                 "height": height,
                                 "interlaced": layout in INTERLACED_LAYOUTS,
                                 "bit_depth": _u(s[0x3301]) if 0x3301 in s else None,
                                 "sample_rate": str(_rational(s[0x3001])) if 0x3001 in s else None})
        elif kind == "sound":
            out["audio"].append({"sample_rate": str(_rational(s[0x3D03])) if 0x3D03 in s else None,
                                 "channels": _u(s[0x3D07]) if 0x3D07 in s else None,
                                 "bits": _u(s[0x3D01]) if 0x3D01 in s else None})
    return out


def _partition_fields(data, b, shifted):
    """Pull Body SID, Index SID and the operational pattern UL out of a partition pack.

    ST 377-1 (Annex G) lays the value out as ..., Index SID (u32 at 40), Body SID
    (u32 at 52), OP UL (at 56). The shifted writer layout used by the fixtures places
    the SIDs on u64 slots instead (Index SID at 48, Body SID at 60) and the OP UL at 64.
    """
    if shifted:
        index_sid, body_sid = _u(data[b + 48:b + 52]), _u(data[b + 60:b + 64])
        op = data[b + 64:b + 80]
    else:
        index_sid, body_sid = _u(data[b + 40:b + 44]), _u(data[b + 52:b + 56])
        op = data[b + 56:b + 72]
    return index_sid, body_sid, op


def _segment_key(s):
    # Two copies of the same index table segment (e.g. repeated in a footer) carry the
    # same Index SID, Body SID, Edit Rate, Start Position and Duration.
    return (s.get(0x3F06), s.get(0x3F07), s.get(0x3F0B), s.get(0x3F0C), s.get(0x3F0D))


def parse(data):
    # A file may carry run-in bytes before the header partition pack. The header pack's
    # key is unambiguous between the standard ST 377-1 layout and the shifted writer
    # layout, so it fixes the layout for every partition in the file.
    start = data.find(RIP[:12])
    while start != -1 and not _is_header_partition(data[start:start + 16]):
        start = data.find(RIP[:12], start + 1)
    if start == -1:
        raise MxfError("no header partition")
    shifted = data[start + 13] != 4
    parts, essence, segments = [], {}, {}
    rip = False
    cur = None
    for key, b, e in klvs(data, start):
        if is_partition(key, shifted):
            index_sid, body_sid, op = _partition_fields(data, b, shifted)
            status = partition_status(key, shifted)
            cur = {"kind": {2: "header", 3: "body", 4: "footer"}[partition_kind(key, shifted)],
                   "closed": status in (2, 4), "complete": status in (3, 4),
                   "body_sid": body_sid, "index_sid": index_sid,
                   "_op": op, "_primer": {}, "_sets": []}
            parts.append(cur)
        elif key == PRIMER:
            cur["_primer"] = _primer(data, b, e)
        elif key[4] == 0x02 and key[5] == 0x53 and key[8:13] == bytes.fromhex("0d01010101") and cur is not None:
            kind = SET_TYPES.get(_u(key[13:15]))
            if kind:
                cur["_sets"].append((kind, _local_set(data, b, e, cur["_primer"])))
        elif key == INDEX:
            s = _local_set(data, b, e, cur["_primer"] if cur else {})
            # Keep one copy per distinct index table segment.
            segments.setdefault(_segment_key(s), s)
        elif key[:12] == bytes.fromhex("060e2b34010201010d010301"):
            kind = ITEMS.get(key[12], "other")
            essence[kind] = essence.get(kind, 0) + 1
        elif key == RIP:
            rip = True
    if not parts:
        raise MxfError("no partitions")
    # Header metadata: prefer the copy in a partition that is both closed and complete;
    # fall back to any partition carrying metadata.
    best = next((p for p in parts if p["closed"] and p["complete"] and p["_sets"]), None)
    if best is None:
        best = next((p for p in parts if p["_sets"]), None)
    meta = _metadata(best["_sets"]) if best else _metadata([])
    op = parts[0]["_op"]
    # OP-Atom carries item wrapping (byte 12 = 0x01) with the Atom item (0x10) in
    # byte 13; the OP1x/2x/3x pattern keys use bytes 13-14 for the pattern pair.
    if op[12] == 0x10:
        pattern = "OP-Atom"
    else:
        pattern = OPS.get((op[12], op[13]), "unknown")
    rate, dur = meta.pop("edit_rate"), meta["duration_edit_units"]
    cbe = {_u(s[0x3F05]) for s in segments.values() if _u(s.get(0x3F05, b"\0" * 4))}
    return {"run_in": start, "operational_pattern": pattern,
            "partitions": [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts], "rip": rip,
            "edit_rate": str(rate) if rate else None, "duration_edit_units": dur,
            "duration": str(dur / rate) if rate and dur is not None else None,
            "video": meta["video"], "audio": meta["audio"], "essence": dict(sorted(essence.items())),
            "index": {"segments": len(segments),
                      "edit_units": sum(int.from_bytes(s.get(0x3F0D, bytes(8)), "big", signed=True) for s in segments.values()),
                      "constant_bytes": cbe.pop() if len(cbe) == 1 else None}}
