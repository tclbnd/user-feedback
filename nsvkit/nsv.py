"""Nullsoft Streaming Video (.nsv): file header, metadata, TOC, sync and non-sync frames."""
import struct
from fractions import Fraction

UNKNOWN = 0xFFFFFFFF


class NsvError(ValueError):
    pass


def framerate(b):
    """The frame-rate byte of a sync header (integer below 0x80, coded fraction above)."""
    return Fraction(b)


def metadata(text):
    out = {}
    for item in text.split():
        if "=" in item:
            key, value = item.split("=", 1)
            out[key] = value.strip("\"'")
    return out


def header(data):
    if data[:4] != b"NSVf":
        return 0, None
    size, file_size, length_ms, meta_len, toc_alloc, toc_size = struct.unpack_from("<6I", data, 4)
    p = 28
    meta = metadata(data[p:p + meta_len].decode("latin-1").rstrip("\0"))
    p += meta_len
    return size, {"file_size": file_size, "length_ms": None if length_ms == UNKNOWN else length_ms,
                  "metadata": meta, "toc_entries": toc_size}


def parse(data):
    start, head = header(data)
    p, n = start, len(data)
    first = None
    frames = syncs = changes = junk = 0
    video = audio = 0
    aux_types = {}
    fps = None
    while p < n:
        if data[p:p + 4] == b"NSVs":
            if p + 19 > n:
                break
            vid, aud = data[p + 4:p + 8], data[p + 8:p + 12]
            w, h, rate = struct.unpack_from("<HHB", data, p + 12)
            fmt = (vid, aud, w, h)
            if first is None:
                first = fmt
                fps = framerate(rate)
            elif fmt != first:
                changes += 1
            syncs += 1
            q = p + 19
        else:
            p += 1                                   # not at a frame: resynchronise
            junk += 1
            continue
        if q + 5 > n:
            break
        word = data[q] | data[q + 1] << 8
        naux, vlen = word & 0x0F, word >> 4
        alen = struct.unpack_from("<H", data, q + 3)[0]
        q += 5
        if q + vlen + alen > n:
            break                                    # truncated frame
        a = q
        for _ in range(naux):
            size = struct.unpack_from("<H", data, a)[0]
            kind = data[a + 2:a + 6].decode("latin-1")
            aux_types[kind] = aux_types.get(kind, 0) + 1
            a += 6 + size
        video += vlen
        audio += alen
        frames += 1
        p = q + vlen + alen
    if first is None:
        raise NsvError("no NSVs sync frame found")
    vid, aud, w, h = first
    return {"header": head, "video_format": vid.decode("latin-1"), "audio_format": aud.decode("latin-1"),
            "width": w, "height": h, "fps": str(fps), "frames": frames, "sync_frames": syncs,
            "format_changes": changes, "video_bytes": video, "audio_bytes": audio, "aux": dict(sorted(aux_types.items())),
            "skipped_bytes": junk, "duration": str(Fraction(frames) / fps) if fps else None}
