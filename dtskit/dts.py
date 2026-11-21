"""DTS Coherent Acoustics core frames in raw .dts streams and DTS-in-WAV/CD images."""
from fractions import Fraction

SYNCS = {
    b"\x7f\xfe\x80\x01": "BE16",
}
RATES = {1: 8000, 2: 16000, 3: 32000, 6: 11025, 7: 22050, 8: 48000, 11: 12000, 12: 24000, 13: 44100}
CHANNELS = [1, 2, 2, 2, 2, 3, 3, 4, 4, 5, 6, 6, 6, 7, 8, 8]
KBPS = [32, 56, 64, 96, 112, 128, 192, 224, 256, 320, 384, 448, 512, 576, 640, 768, 960, 1024,
        1152, 1280, 1344, 1408, 1411.2, 1472, 1536, 1920, 2048, 3072, 3840]
EXT = {0: "XCh", 2: "X96", 6: "XXCh"}


class DtsError(ValueError):
    pass


def _words(data, pos, layout, nbytes):
    """Return the frame's bit stream (as an int and bit count) normalised to big-endian 16-bit words."""
    raw = data[pos:pos + nbytes]
    if layout.startswith("LE"):
        raw = b"".join(raw[i + 1:i + 2] + raw[i:i + 1] for i in range(0, len(raw) - 1, 2))
    if layout.endswith("14"):
        bits, n = 0, 0
        for i in range(0, len(raw) - 1, 2):
            bits = (bits << 14) | (((raw[i] << 8) | raw[i + 1]) & 0x3FFF)
            n += 14
        return bits, n
    return int.from_bytes(raw, "big"), len(raw) * 8


class Bits:
    def __init__(self, value, count):
        self.v, self.n, self.p = value, count, 0

    def u(self, k):
        self.p += k
        if self.p > self.n:
            raise DtsError("truncated frame header")
        return (self.v >> (self.n - self.p)) & ((1 << k) - 1)


def header(data, pos, layout):
    probe = 16 if layout.endswith("16") else 20
    b = Bits(*_words(data, pos, layout, probe))
    b.u(32)
    if layout.endswith("14"):
        b.u(6)                                  # rest of the 14-bit sync word
    ftype, short, cpf, nblks, fsize = b.u(1), b.u(5), b.u(1), b.u(7), b.u(14)
    amode, sfreq, rate = b.u(6), b.u(4), b.u(5)
    b.u(1); b.u(1); b.u(1); b.u(1); b.u(1)      # MIX DYNF TIMEF AUXF HDCD
    ext_id, ext, b_aspf, lff = b.u(3), b.u(1), b.u(1), b.u(2)
    if nblks < 5 or fsize < 95 or sfreq not in RATES:
        return None
    size = fsize
    file_bytes = size * 8 // 7 if layout.endswith("14") else size
    return {"normal": bool(ftype), "samples": nblks * 32, "frame_bytes": size, "file_bytes": file_bytes,
            "channels": CHANNELS[amode] if amode < 16 else None, "lfe": False,
            "sample_rate": RATES[sfreq], "kbps": KBPS[rate] if rate < len(KBPS) else None,
            "crc": bool(cpf), "extension": EXT.get(ext_id) if ext else None}


def scan(data):
    pos, frames, layout, first = 0, [], None, None
    while pos + 20 <= len(data):
        lay = SYNCS.get(data[pos:pos + 4])
        if lay == "BE14" and not (data[pos + 4] == 0x07 and data[pos + 5] & 0xF0 == 0xF0):
            lay = None
        if lay == "LE14" and not (data[pos + 5] == 0x07 and data[pos + 4] & 0xF0 == 0xF0):
            lay = None
        if lay and (layout is None or lay == layout):
            h = header(data, pos, lay)
            if h and pos + h["file_bytes"] <= len(data):
                layout = layout or lay
                first = first or h
                frames.append(h)
                pos += h["file_bytes"]
                continue
        if frames:
            break
        pos += 1
    if not frames:
        raise DtsError("no DTS core frames")
    samples = sum(f["samples"] for f in frames)
    exts = sorted({f["extension"] for f in frames if f["extension"]})
    return {"layout": layout, "frames": len(frames), "sample_rate": first["sample_rate"],
            "channels": first["channels"], "lfe": first["lfe"], "bitrate_kbps": first["kbps"],
            "samples_per_frame": first["samples"], "crc": first["crc"], "extensions": exts,
            "duration": str(Fraction(samples, first["sample_rate"])),
            "variable_frame_size": len({f["frame_bytes"] for f in frames}) > 1}
