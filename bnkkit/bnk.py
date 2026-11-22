"""Audiokinetic Wwise SoundBanks (.bnk): BKHD, DIDX/DATA media, HIRC objects, STID names."""
import struct

HIRC_TYPES = {1: "state", 2: "sound", 3: "action", 4: "event", 5: "random_sequence",
              6: "switch", 7: "actor_mixer", 8: "bus", 9: "blend", 10: "music_segment",
              11: "music_track", 12: "music_switch", 13: "music_playlist",
              14: "attenuation"}
SOURCE_TYPES = (2, 11)  # sound objects and music tracks both carry a media source
STREAM = {0: "embedded", 1: "prefetch", 2: "streamed"}

# Layout breakpoints expressed as BKHD bank versions.
LEGACY_SOURCE_MAX = 88   # <= 88: stream type is a u32; newer banks use a u8
BYTE_EVENT_MIN = 122    # >= 122: event action count is a u8; older banks use a u32

KNOWN_VERSION_MAX = 0x10000  # sanity ceiling used while guessing endianness


class BnkError(ValueError):
    pass


class _Reader:
    """Structured reads in the bank's own endianness."""

    def __init__(self, data, prefix):
        self.data = data
        self.prefix = prefix  # "<" or ">"

    def take(self, fmt, pos):
        return struct.unpack_from(self.prefix + fmt, self.data, pos)

    def u8(self, pos):
        return self.data[pos]

    def u32(self, pos):
        return self.take("I", pos)[0]


def _detect_header(data):
    """Return (reader, endian name, BKHD body pos, BKHD body size).

    The BKHD size and version are read both ways; an interpretation is only
    viable if the chunk actually fits the file and the version looks like a
    real Wwise bank version.  Little-endian wins on a tie.
    """
    if len(data) < 12 or data[:4] != b"BKHD":
        raise BnkError("not a Wwise SoundBank")
    candidates = []
    for prefix, name in (("<", "little"), (">", "big")):
        size, version = struct.unpack_from(prefix + "II", data, 4)
        if 8 + size <= len(data) and 0 < version < KNOWN_VERSION_MAX:
            candidates.append((prefix, name, size))
    if not candidates:
        raise BnkError("BKHD chunk runs past the end of the file")
    prefix, name, size = candidates[0]
    return _Reader(data, prefix), name, 8, size


def parse(data):
    reader, endian, bkhd_body, bkhd_size = _detect_header(data)
    if bkhd_size < 8:
        raise BnkError("BKHD chunk is too small for version and bank id")
    version = reader.u32(bkhd_body)
    bank_id = reader.u32(bkhd_body + 4)

    out = {"version": version, "bank_id": bank_id, "endian": endian,
           "bank_name": None, "chunks": [],
           "media": [], "data_bytes": 0, "media_outside_data": False,
           "objects": {}, "sounds": {}, "events": [], "missing_media": []}

    # Walk the chunk list once, validating every chunk against the file length.
    chunks = []
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise BnkError("truncated chunk header at offset %d" % pos)
        tag = data[pos:pos + 4].decode("latin-1")
        size = reader.u32(pos + 4)
        body = pos + 8
        if body + size > len(data):
            raise BnkError("chunk %s runs past the end of the file" % tag)
        chunks.append((tag, body, size))
        out["chunks"].append(tag)
        pos = body + size

    didx_entries = []
    data_at = None
    for tag, body, size in chunks:
        if tag == "DIDX":
            if size % 12:
                raise BnkError("DIDX size %d is not a multiple of 12" % size)
            for i in range(size // 12):
                mid, off, msize = reader.take("III", body + i * 12)
                didx_entries.append((mid, off, msize))
        elif tag == "DATA":
            data_at = body
            out["data_bytes"] = size
        elif tag == "STID":
            _parse_stid(reader, data, body, size, bank_id, out)

    # DIDX offsets are relative to the start of the DATA payload, not the file.
    media_ids = set()
    data_bytes = out["data_bytes"]
    for mid, off, msize in didx_entries:
        media_ids.add(mid)
        out["media"].append({"id": mid, "offset": off, "size": msize})
        if data_at is None or off > data_bytes or off + msize > data_bytes:
            out["media_outside_data"] = True

    missing = set()
    for tag, body, size in chunks:
        if tag == "HIRC":
            _parse_hirc(reader, data, body, size, version, media_ids, out, missing)

    out["objects"] = dict(sorted(out["objects"].items()))
    out["sounds"] = dict(sorted(out["sounds"].items()))
    out["missing_media"] = sorted(missing)
    return out


def _parse_stid(reader, data, body, size, bank_id, out):
    if size < 8:
        raise BnkError("STID chunk is too small for its header")
    count = reader.u32(body + 4)
    end = body + size
    p = body + 8
    for _ in range(count):
        if p + 5 > end:
            raise BnkError("truncated STID entry")
        sid = reader.u32(p)
        n = reader.u8(p + 4)  # name length is one byte
        p += 5
        if p + n > end:
            raise BnkError("STID name runs past the end of the chunk")
        name = data[p:p + n].decode("utf-8", "replace")
        p += n
        if sid == bank_id:
            out["bank_name"] = name


def _parse_hirc(reader, data, body, size, version, media_ids, out, missing):
    end = body + size
    if body + 4 > end:
        raise BnkError("HIRC chunk is too small for its object count")
    count = reader.u32(body)
    p = body + 4
    for _ in range(count):
        if p + 9 > end:
            raise BnkError("truncated HIRC object header")
        otype = reader.u8(p)
        osize = reader.u32(p + 1)   # object size includes the 4-byte object id
        oid = reader.u32(p + 5)
        if osize < 4:
            raise BnkError("HIRC object %d is smaller than its id" % oid)
        obj_end = p + 5 + osize
        if obj_end > end:
            raise BnkError("HIRC object %d runs past the end of the chunk" % oid)
        obj_body = p + 9

        name = HIRC_TYPES.get(otype, "type_%d" % otype)
        out["objects"][name] = out["objects"].get(name, 0) + 1

        if otype in SOURCE_TYPES:
            kind, source = _parse_source(reader, obj_body, version)
            if otype == 2:  # only sound objects feed the sound counts
                out["sounds"][kind] = out["sounds"].get(kind, 0) + 1
            if kind in ("embedded", "prefetch") and source not in media_ids:
                missing.add(source)
        elif otype == 4:
            actions = _parse_event(reader, obj_body, version, obj_end)
            out["events"].append({"id": oid, "actions": actions})

        p = obj_end


def _parse_source(reader, pos, version):
    """Sound/music-track source header.  Returns (source kind, media id)."""
    if version <= LEGACY_SOURCE_MAX:
        _plugin, stream, source = reader.take("III", pos)
    else:
        _plugin = reader.u32(pos)
        stream = reader.u8(pos + 4)       # stream type shrank to one byte
        source = reader.u32(pos + 5)
    return STREAM.get(stream, "type_%d" % stream), source


def _parse_event(reader, pos, version, obj_end):
    if version >= BYTE_EVENT_MIN:
        count = reader.u8(pos)
        p = pos + 1
    else:
        count = reader.u32(pos)
        p = pos + 4
    if p + 4 * count > obj_end:
        raise BnkError("event action list runs past the end of its object")
    return list(reader.take("%dI" % count, p))
