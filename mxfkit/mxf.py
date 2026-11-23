"""MXF (SMPTE ST 377-1) files: KLV, partitions, header metadata, index tables, essence."""
from fractions import Fraction

PREFIX = bytes.fromhex("060e2b34")
RIP_PREFIX = bytes.fromhex("060e2b3402050101" "0d01020101")
PRIMER_PREFIX = bytes.fromhex("060e2b3402050101" "0d01020101")
INDEX_PREFIX = bytes.fromhex("060e2b3402530101" "0d01020101")
PARTITION_PREFIX = bytes.fromhex("060e2b3402050101" "0d01020101")
METADATA_PREFIX = bytes.fromhex("060e2b3402530101" "0d01010101")
ESSENCE_PREFIX = bytes.fromhex("060e2b3401020101" "0d010301")
# structural metadata sets, by the byte pair 13-14 of their key (SMPTE ST 377-1 / RP 210)
SET_TYPES = {0x0F00: "sequence", 0x1100: "sourceclip", 0x1300: "filler", 0x1400: "timecode",
             0x3600: "material", 0x3700: "source", 0x3B00: "track",
             0x2700: "picture", 0x2800: "picture", 0x2900: "picture", 0x5100: "picture",
             0x4200: "sound", 0x4700: "sound", 0x4800: "sound", 0x4400: "multiple"}
PICTURE_DEF = bytes.fromhex("060e2b34040101010103020201000000")
OPS = {(1, 1): "OP1a", (1, 2): "OP1b", (1, 3): "OP1c", (2, 1): "OP2a", (2, 2): "OP2b", (2, 3): "OP2c",
       (3, 1): "OP3a", (3, 2): "OP3b", (3, 3): "OP3c", (0x10, 0): "OP-Atom"}
# Essence element item types: the 0x04-0x07 set and the generic container 0x14-0x18 set
# that ST 377-1 defines for the same kinds of essence
ITEMS = {0x04: "system", 0x05: "picture", 0x06: "sound", 0x07: "data",
         0x14: "system", 0x15: "picture", 0x16: "sound", 0x17: "data", 0x18: "compound"}
# a FrameLayout of SeparateFields, OneField or MixedFields stores one field at a time, so
# StoredHeight is a field height rather than the height of the whole frame
FIELD_HEIGHT = (1, 2, 3)
INTERLACED = (1, 2, 3)
UNKNOWN = (0, 0xFFFFFFFFFFFFFFFF)   # an unset or still unknown length property

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
# a primer pack labels a property by its UL.  The dictionary version byte (at index 7)
# differs between writers, so also index by the label without it, but only where that
# does not become ambiguous
UL_TAGS = {}
for _tag, _label in UL.items():
    UL_TAGS.setdefault(_label[4:], _tag)
_labels = {}
for _tag, _label in UL.items():
    _labels.setdefault(_label[4:7] + _label[8:], set()).add(_tag)
for _label, _tags in _labels.items():
    if len(_tags) == 1:
        UL_TAGS.setdefault(_label, next(iter(_tags)))


class MxfError(ValueError):
    pass


def ber(data, pos):
    """Return (length, value position) of a BER coded length."""
    if pos >= len(data):
        raise MxfError("truncated BER length at %d" % pos)
    b = data[pos]
    if b < 0x80:
        return b, pos + 1
    n = b & 0x7F
    if n > 8 or pos + 1 + n > len(data):
        raise MxfError("bad BER length at %d" % pos)
    return int.from_bytes(data[pos + 1:pos + 1 + n], "big"), pos + 1 + n


def klvs(data, start=0):
    """Yield (key, value start, value end) for every KLV of the file.

    Anything that is not a KLV key (run-in, padding after the last KLV) is skipped.
    """
    pos = start
    while pos + 17 <= len(data):
        if data[pos:pos + 4] != PREFIX:
            pos = data.find(PREFIX, pos + 1)
            if pos < 0:
                return
            continue
        key = data[pos:pos + 16]
        length, v = ber(data, pos + 16)
        if v + length > len(data):
            # a truncated KLV: nothing after it can be trusted, but a later partition may be intact
            pos = data.find(PARTITION_PREFIX, pos + 1)
            if pos < 0:
                return
            continue
        yield key, v, v + length
        pos = v + length


def is_partition(key):
    """Partition pack key: ...01.<kind: header/body/footer>.<status>.00."""
    return key[:13] == PARTITION_PREFIX and key[13] in (2, 3, 4)


def is_essence(key):
    return key[:12] == ESSENCE_PREFIX


def is_metadata_set(key):
    return key[:13] == METADATA_PREFIX


def is_index_segment(key):
    return key[:13] == INDEX_PREFIX


def is_primer(key):
    return key[:13] == PRIMER_PREFIX and key[13] == 0x05


def is_rip(key):
    return key[:13] == RIP_PREFIX and key[13] == 0x11


def _same_uid(a, b):
    """Compare two ULs, ignoring the registry version byte that writers vary."""
    return a is not None and len(a) >= 16 and (a == b or a[4:7] + a[8:16] == b[4:7] + b[8:16])


def _u(raw):
    return int.from_bytes(raw, "big")


def _s(raw):
    return int.from_bytes(raw, "big", signed=True)


def _rational(raw):
    """SMPTE ST 377-1 Rational: an int32 numerator and denominator."""
    if raw is None or len(raw) < 8:
        return None
    num, den = _s(raw[:4]), _s(raw[4:8])
    return Fraction(num, den) if den else None


def _refs(raw):
    if raw is None or len(raw) < 8:
        return []
    count, size = _u(raw[:4]), _u(raw[4:8])
    if not size:
        return []
    return [raw[8 + i * size:8 + (i + 1) * size] for i in range(count)]


def _known(raw):
    """Whether a UInt64 length property is set, rather than unknown (all ones) or unset (0)."""
    return raw is not None and len(raw) >= 8 and _u(raw) not in UNKNOWN


def _canonical(tag, primer):
    """Local tag of a property, resolving a dynamic tag through the primer pack."""
    if tag < 0x8000:
        return tag if tag in UL else None
    label = primer.get(tag)
    if label is None:
        return None
    return UL_TAGS.get(label[4:]) or UL_TAGS.get(label[4:7] + label[8:])


def _local_set(data, b, e, primer):
    props, p = {}, b
    while p + 4 <= e:
        tag, n = _u(data[p:p + 2]), _u(data[p + 2:p + 4])
        if p + 4 + n > e:
            break
        tag = _canonical(tag, primer)
        if tag is not None:
            props[tag] = data[p + 4:p + 4 + n]
        p += 4 + n
    return props


def _primer(data, b, e):
    count, size = _u(data[b:b + 4]), _u(data[b + 4:b + 8])
    out = {}
    for i in range(count):
        p = b + 8 + i * size
        if size < 18 or p + 18 > e:
            break
        out[_u(data[p:p + 2])] = data[p + 2:p + 18]
    return out


def _timecode(comp, edit_rate):
    """HH:MM:SS:FF of a timecode component; ';' before the frames when drop-frame."""
    frames = _s(comp.get(0x1501) or b"")        # StartTimecode, a count of base units
    base = _u(comp.get(0x1502) or b"")          # RoundedTimecodeBase
    drop = bool(_u(comp.get(0x1503) or b""))    # DropFrame
    if not base:
        base = int(edit_rate) if edit_rate else 30
    if drop:
        # a drop-frame count leaves out the first frame numbers of every minute but every tenth,
        # so the displayed label runs that many ahead of the counted frames
        dropped = base // 15
        per_minute, per_ten = base * 60 - dropped, base * 600 - dropped * 9
        tens, rest = divmod(frames, per_ten)
        frames += dropped * 9 * tens + (dropped * ((rest - dropped) // per_minute) if rest >= dropped else 0)
    seconds, ff = divmod(frames, base)
    return "%02d:%02d:%02d%s%02d" % (seconds // 3600 % 24, seconds // 60 % 60, seconds % 60,
                                     ";" if drop else ":", ff)


def _metadata(sets):
    by_uid = {s[0x3C0A]: (kind, s) for kind, s in sets if 0x3C0A in s}
    out = {"edit_rate": None, "duration_edit_units": None, "video": [], "audio": [], "start_timecode": None}

    # the material package describes the clip itself; source packages the material it came from.
    # An OP-Atom essence file has only a source package, so fall back on those.
    material = next((s for kind, s in sets if kind == "material"), None)
    packages = [material] if material is not None else [s for kind, s in sets if kind == "source"]
    tracks = [by_uid[ref][1] for package in packages for ref in _refs(package.get(0x4403))
              if by_uid.get(ref, (None,))[0] == "track"]
    if not tracks:
        tracks = [s for kind, s in sets if kind == "track"]

    picture = None
    for track in tracks:
        kind, sequence = by_uid.get(track.get(0x4803), (None, {}))
        if kind == "sequence" and _same_uid(sequence.get(0x0201), PICTURE_DEF):
            picture = track, sequence
            break
    if picture is None:     # an audio-only file has no picture track to time the clip by
        picture = next(((track, sequence) for track in tracks
                        for kind, sequence in (by_uid.get(track.get(0x4803), (None, {})),)
                        if kind == "sequence"), None)
    if picture is not None:
        track, sequence = picture
        out["edit_rate"] = _rational(track.get(0x4B01))
        if _known(sequence.get(0x0202)):
            out["duration_edit_units"] = _u(sequence[0x0202])

    for track in tracks:
        kind, component = by_uid.get(track.get(0x4803), (None, {}))
        if kind == "sequence":
            components = [by_uid.get(ref, (None, {})) for ref in _refs(component.get(0x1001))]
        elif kind == "timecode":
            components = [(kind, component)]
        else:
            continue
        timecode = next((s for k, s in components if k == "timecode"), None)
        if timecode is not None:
            out["start_timecode"] = _timecode(timecode, out["edit_rate"])
            break

    pictures = [s for kind, s in sets if kind == "picture"]
    # when a file is still open the sequence length is unknown; the container duration of
    # the picture descriptor then carries it
    if out["duration_edit_units"] is None:
        for pic in pictures:
            if _known(pic.get(0x3002)):
                out["duration_edit_units"] = _u(pic[0x3002])
                break
    if out["edit_rate"] is None:
        out["edit_rate"] = next((r for r in (_rational(pic.get(0x3001)) for pic in pictures) if r), None)

    for pic in pictures:
        layout = _u(pic[0x320C]) if 0x320C in pic else None
        height = _u(pic[0x3202]) if 0x3202 in pic else 0
        out["video"].append({"width": _u(pic[0x3203]) if 0x3203 in pic else None,
                             # fields stored one at a time give a field height, not the frame height
                             "height": height * 2 if layout in FIELD_HEIGHT else height,
                             "interlaced": layout in INTERLACED,
                             "bit_depth": _u(pic[0x3301]) if 0x3301 in pic else None,
                             "sample_rate": str(_rational(pic.get(0x3001))) if 0x3001 in pic else None})
    for kind, s in sets:
        if kind == "sound":
            rate = _rational(s.get(0x3D03))
            out["audio"].append({"sample_rate": str(rate) if rate is not None else None,
                                 "channels": _u(s[0x3D07]) if 0x3D07 in s else None,
                                 "bits": _u(s[0x3D01]) if 0x3D01 in s else None})
    return out


def parse(data):
    start = _header_offset(data)
    parts, essence, segments = [], {}, {}
    rip, primer, cur = False, {}, None
    for key, b, e in klvs(data, start):
        if is_partition(key):
            status = key[14]
            cur = {"kind": {2: "header", 3: "body", 4: "footer"}.get(key[13], "body"),
                   "closed": status in (2, 4), "complete": status in (3, 4),
                   "body_sid": _u(data[b + 60:b + 64]), "index_sid": _u(data[b + 48:b + 52]),
                   "_op": data[b + 64:b + 80], "_sets": []}
            parts.append(cur)
            primer = {}     # a primer pack only labels the partition it sits in
        elif is_primer(key):
            primer = _primer(data, b, e)
        elif is_index_segment(key):
            seg = _local_set(data, b, e, primer)
            # later partitions repeat the same segment: count each distinct one once
            segments.setdefault(seg.get(0x3C0A) or tuple(sorted(seg.items())), seg)
        elif is_essence(key):
            kind = ITEMS.get(key[12], "other")
            essence[kind] = essence.get(kind, 0) + 1
        elif is_metadata_set(key):
            kind = SET_TYPES.get(_u(key[13:15]))
            if kind and cur is not None:
                cur["_sets"].append((kind, _local_set(data, b, e, primer)))
        elif is_rip(key):
            rip = True
    if not parts:
        raise MxfError("no partitions")
    # header metadata: the copy in a closed and complete partition beats one in an open or
    # incomplete partition; among equals, the earliest partition in the file wins
    best = None
    for part in parts:
        if part["_sets"] and (best is None or
                              (part["closed"], part["complete"]) > (best["closed"], best["complete"])):
            best = part
    meta = _metadata(best["_sets"] if best else [])
    op = parts[0]["_op"]
    pattern = OPS.get((op[12], op[13]), "unknown") if len(op) >= 14 else "unknown"
    rate, dur = meta.pop("edit_rate"), meta["duration_edit_units"]
    return {"run_in": start, "operational_pattern": pattern,
            "partitions": [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts], "rip": rip,
            "edit_rate": str(rate) if rate else None, "duration_edit_units": dur,
            "duration": str(Fraction(dur) / rate) if rate and dur is not None else None,
            "start_timecode": meta["start_timecode"], "video": meta["video"], "audio": meta["audio"],
            "essence": dict(sorted(essence.items())), "index": _index(segments)}


def _header_offset(data):
    """Byte offset of the header partition pack; anything before it is run-in."""
    if is_partition(data[:16]):
        return 0
    pos = data.find(PARTITION_PREFIX)
    while pos >= 0:
        if pos + 16 <= len(data) and is_partition(data[pos:pos + 16]):
            return pos
        pos = data.find(PARTITION_PREFIX, pos + 1)
    raise MxfError("no header partition")


def _index(segments):
    """Distinct index table segments, the edit units they cover and their constant byte count."""
    spans, counts = [], set()
    for seg in segments.values():
        begin = _s(seg[0x3F0C]) if len(seg.get(0x3F0C) or b"") >= 8 else 0
        length = _s(seg[0x3F0D]) if len(seg.get(0x3F0D) or b"") >= 8 else None
        if length is not None and length > 0:
            spans.append((begin, begin + length))
        counts.add(_u(seg[0x3F05]) if len(seg.get(0x3F05) or b"") >= 4 else 0)
    # segments may overlap where a writer repeats an index; count every edit unit once
    covered, end = 0, None
    for lo, hi in sorted(spans):
        if end is None or lo > end:
            covered += hi - lo
        elif hi > end:
            covered += hi - end
        end = hi if end is None else max(end, hi)
    count = next(iter(counts)) if len(counts) == 1 else 0
    return {"segments": len(segments), "edit_units": covered, "constant_bytes": count or None}

