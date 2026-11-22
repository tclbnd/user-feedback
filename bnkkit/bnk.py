"""Audiokinetic Wwise SoundBanks (.bnk): BKHD, DIDX/DATA media, HIRC objects, STID names."""
import struct

HIRC_TYPES = {1: "state", 2: "sound", 3: "action", 4: "event", 5: "random_sequence", 6: "switch", 7: "actor_mixer",
              8: "bus", 9: "blend", 10: "music_segment", 11: "music_track", 12: "music_switch",
              13: "music_playlist", 14: "attenuation"}
STREAM = {0: "embedded", 1: "prefetch", 2: "streamed"}


class BnkError(ValueError):
    pass


def _chunks(data):
    pos, out = 0, []
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4].decode("latin-1")
        size, = struct.unpack_from("<I", data, pos + 4)
        if pos + 8 + size > len(data):
            raise BnkError("chunk %s runs past the end of the file" % tag)
        out.append((tag, pos + 8, size))
        pos += 8 + size
    return out


def parse(data):
    chunks = _chunks(data)
    if not chunks or chunks[0][0] != "BKHD":
        raise BnkError("not a Wwise SoundBank")
    _, b, _ = chunks[0]
    version, bank_id = struct.unpack_from("<II", data, b)
    out = {"version": version, "bank_id": bank_id, "bank_name": None, "chunks": [c[0] for c in chunks],
           "media": [], "data_bytes": 0, "media_outside_data": False, "objects": {}, "sounds": {},
           "events": [], "missing_media": []}
    didx = data_at = None
    for tag, b, size in chunks:
        if tag == "DIDX":
            didx = [struct.unpack_from("<III", data, b + i) for i in range(0, size - size % 12, 12)]
        elif tag == "DATA":
            data_at, out["data_bytes"] = b, size
        elif tag == "STID":
            kind, count = struct.unpack_from("<II", data, b)
            p = b + 8
            for _ in range(count):
                sid, n = struct.unpack_from("<IB", data, p)
                name = data[p + 5:p + 5 + n].decode("utf-8", "replace")
                p += 5 + n
                if sid == bank_id:
                    out["bank_name"] = name
    media_ids = set()
    for mid, off, size in didx or []:
        media_ids.add(mid)
        out["media"].append({"id": mid, "offset": off, "size": size})
        if data_at is None or off + size > out["data_bytes"]:
            out["media_outside_data"] = True
    for tag, b, size in chunks:
        if tag != "HIRC":
            continue
        count, = struct.unpack_from("<I", data, b)
        p = b + 4
        for _ in range(count):
            otype, osize, oid = struct.unpack_from("<BII", data, p)
            body = p + 9
            name = HIRC_TYPES.get(otype, "type_%d" % otype)
            out["objects"][name] = out["objects"].get(name, 0) + 1
            if otype == 2:
                if version <= 88:
                    plugin, stream, source = struct.unpack_from("<III", data, body)
                else:
                    plugin, stream, source = struct.unpack_from("<IBI", data, body)
                kind = STREAM.get(stream, "type_%d" % stream)
                out["sounds"][kind] = out["sounds"].get(kind, 0) + 1
                if kind in ("embedded", "prefetch") and source not in media_ids \
                        and source not in out["missing_media"]:
                    out["missing_media"].append(source)
            elif otype == 4:
                if version >= 122:
                    n = data[body]
                    ids = struct.unpack_from("<%dI" % n, data, body + 1)
                else:
                    n, = struct.unpack_from("<I", data, body)
                    ids = struct.unpack_from("<%dI" % n, data, body + 4)
                out["events"].append({"id": oid, "actions": list(ids)})
            p = p + 5 + osize
    out["objects"] = dict(sorted(out["objects"].items()))
    out["sounds"] = dict(sorted(out["sounds"].items()))
    out["missing_media"].sort()
    return out
