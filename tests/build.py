"""WAV builder with cue, adtl, plst, smpl and inst chunks."""
import struct


def ck(cid, body):
    out = cid.encode() + struct.pack("<I", len(body)) + body
    return out + (b"\0" if len(body) & 1 else b"")


def fmt(ch=2, rate=48000, bits=24):
    align = ch * bits // 8
    return ck("fmt ", struct.pack("<HHIIHH", 1, ch, rate, rate * align, align, bits))


def data(frames, ch=2, bits=24):
    return ck("data", bytes((i * 7) & 0xFF for i in range(frames * ch * bits // 8)))


def cue(points):
    """points: (id, sample) or (id, sample, position)."""
    body = struct.pack("<I", len(points))
    for p in points:
        cid, sample = p[0], p[1]
        pos = p[2] if len(p) > 2 else sample
        body += struct.pack("<II4sIII", cid, pos, b"data", 0, 0, sample)
    return ck("cue ", body)


def adtl(labels=(), notes=(), spans=()):
    """labl/note entries are (cue, text); spans are (cue, length, text)."""
    body = b"adtl"
    for cid, text in labels:
        body += ck("labl", struct.pack("<I", cid) + text.encode("latin-1") + b"\0")
    for cid, text in notes:
        body += ck("note", struct.pack("<I", cid) + text.encode("latin-1") + b"\0")
    for cid, length, text in spans:
        body += ck("ltxt", struct.pack("<II4sHHHH", cid, length, b"rgn ", 0, 0, 0, 0) + text.encode("latin-1") + b"\0")
    return ck("LIST", body)


def info(**fields):
    body = b"INFO"
    for k, v in fields.items():
        body += ck(k, v.encode("latin-1") + b"\0")
    return ck("LIST", body)


def plst(items):
    return ck("plst", struct.pack("<I", len(items)) + b"".join(struct.pack("<III", *i) for i in items))


def smpl(loops, note=60, pitch=0, period=20833, manufacturer=0, product=0,
         smpte_format=0, smpte_offset=0):
    """loops: (cue, type, start, end, play_count[, fraction])."""
    body = struct.pack("<9I", manufacturer, product, period, note, pitch,
                       smpte_format, smpte_offset, len(loops), 0)
    for loop in loops:
        cue_id, ltype, start, end, count = loop[:5]
        fraction = loop[5] if len(loop) > 5 else 0
        body += struct.pack("<6I", cue_id, ltype, start, end, fraction, count)
    return ck("smpl", body)


def inst(note=60, detune=0, gain=0, lo=0, hi=127, lv=1, hv=127):
    return ck("inst", struct.pack("<BbbBBBB", note, detune, gain, lo, hi, lv, hv))


def wav(*parts, frames=48000, ch=2, bits=24, rate=48000, trailing=b""):
    body = b"WAVE" + fmt(ch, rate, bits) + data(frames, ch, bits) + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body + trailing
