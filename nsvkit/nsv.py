"""Nullsoft Streaming Video (.nsv): file header, metadata, TOC, sync and non-sync frames."""
import struct
from fractions import Fraction

UNKNOWN = 0xFFFFFFFF

SYNC = b"NSVs"          # a frame that carries a new stream header ...
SYNC_HEADER = 19        # ... which is 19 bytes long
FRAME = b"\xEF\xBE"     # every other frame starts with this marker
FRAME_MARKER = 2
FRAME_HEADER = 5        # 20-bit video size + 4-bit aux count, 16-bit audio size
AUX_HEADER = 6          # 16-bit size + 4-byte type


class NsvError(ValueError):
    pass


def framerate(code):
    """The frame rate announced by a sync header's frame-rate byte.

    Every value below 0x80 is a plain integer rate.  Rates that need a
    fraction set the top bit; the rest of the byte picks the rate the way the
    Nullsoft encoder codes it (30, 25 and 24 base rates, the 1000/1001 NTSC
    factor and a divisor for the slower rates).
    """
    if code < 0x80:
        return Fraction(code)
    n = (code >> 2) & 0x1F
    num, den = (1, n + 1) if n < 16 else (n - 15, 1)
    if code & 1:
        num, den = num * 1000, den * 1001
    return Fraction(num * {2: 25, 3: 24}.get(code & 3, 30), den)


def metadata(text):
    """Name/value pairs, whitespace separated; values may be quoted."""
    out = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        start = i
        while i < n and text[i] != "=" and not text[i].isspace():
            i += 1
        key = text[start:i]
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != "=":
            continue
        i += 1
        if i < n and text[i] in "\"'":
            quote = text[i]
            i += 1
            start = i
            while i < n and text[i] != quote:
                i += 1
            value = text[start:i]
            i += 1
        else:
            start = i
            while i < n and not text[i].isspace():
                i += 1
            value = text[start:i]
        if key:
            out[key] = value
    return out


def header(data):
    if data[:4] != b"NSVf":
        return 0, None
    size, file_size, length_ms, meta_len, toc_alloc, toc_size = struct.unpack_from("<6I", data, 4)
    meta = metadata(data[28:28 + meta_len].decode("latin-1").replace("\0", " "))
    return size, {"file_size": file_size, "length_ms": None if length_ms == UNKNOWN else length_ms,
                  "metadata": meta, "toc_entries": toc_size}


def frame_header(data, q, n):
    """Decode the frame header at q: (video size, audio size, frame end, aux chunks)."""
    body = q + FRAME_HEADER
    if body > n:
        return None
    word = data[q] | data[q + 1] << 8 | data[q + 2] << 16
    naux, vlen = word & 0x0F, word >> 4
    alen = struct.unpack_from("<H", data, q + 3)[0]
    end = body + vlen + alen
    if end > n:
        return None
    chunks, a = [], body
    for _ in range(naux):                            # auxiliary chunks lead the video data
        if a + AUX_HEADER > body + vlen:
            return None
        size = struct.unpack_from("<H", data, a)[0]
        if a + AUX_HEADER + size > body + vlen:
            return None
        chunks.append((data[a + 2:a + 6].decode("latin-1"), size))
        a += AUX_HEADER + size
    return vlen, alen, end, chunks


def parse(data):
    start, head = header(data)
    p, n = start, len(data)
    first = None
    fps = None
    frames = syncs = changes = skipped = 0
    video = audio = aux_bytes = largest = audio_frames = 0
    aux_types = {}
    while p < n:
        if data[p:p + 4] == SYNC:
            sync, q = True, p + SYNC_HEADER
        elif data[p:p + 2] == FRAME:
            sync, q = False, p + FRAME_MARKER
        else:
            p += 1                                   # not at a frame: resynchronise
            skipped += 1
            continue
        frame = frame_header(data, q, n)
        if frame is None:                            # truncated frame
            p += 1
            skipped += 1
            continue
        vlen, alen, end, chunks = frame
        if sync:
            vid, aud = data[p + 4:p + 8], data[p + 8:p + 12]
            w, h, rate = struct.unpack_from("<HHB", data, p + 12)
            fmt = (vid, aud, w, h)
            if first is None:
                first = fmt
                fps = framerate(rate)
            elif fmt != first:
                changes += 1
            syncs += 1
        extra = 0
        for kind, size in chunks:
            aux_types[kind] = aux_types.get(kind, 0) + 1
            aux_bytes += size
            extra += AUX_HEADER + size
        payload = vlen - extra                       # auxiliary data is not video
        video += payload
        audio += alen
        largest = max(largest, payload)
        if alen:
            audio_frames += 1
        frames += 1
        p = end
    if first is None:
        raise NsvError("no NSVs sync frame found")
    vid, aud, w, h = first
    return {"header": head, "video_format": vid.decode("latin-1"), "audio_format": aud.decode("latin-1"),
            "width": w, "height": h, "fps": str(fps), "frames": frames, "sync_frames": syncs,
            "format_changes": changes, "video_bytes": video, "audio_bytes": audio, "aux": dict(sorted(aux_types.items())),
            "aux_bytes": aux_bytes, "largest_video_frame": largest, "audio_frames": audio_frames,
            "skipped_bytes": skipped, "duration": str(Fraction(frames) / fps) if fps else None}
