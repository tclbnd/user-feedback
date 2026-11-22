"""DTS Coherent Acoustics core frames in raw .dts streams and DTS-in-WAV/CD images."""
import re
from fractions import Fraction

SYNC_WORD = 0x7FFE8001
SYNC16 = {
    b"\x7f\xfe\x80\x01": "BE16",
    b"\xfe\x7f\x01\x80": "LE16",
}
# A 14-bit stream stores 14 data bits in each 16-bit word; the two spare bits are not
# part of the stream.  The 32-bit sync word fills the first two 14-bit words plus four
# bits; six one bits complete the 14-bit sync, so only the 28 payload bits are compared
# (the spare bits may hold any value).  The 14-bit patterns below match the payload
# bits of the two sync words: 0x1FFF then 0x2800, with either byte order.
SYNC14_FIRST = (SYNC_WORD >> 18) & 0x3FFF      # first 14 bits:  0x1FFF
SYNC14_SECOND = (SYNC_WORD >> 4) & 0x3FFF      # next 14 bits:   0x2800
PAD14 = 0x3F                                   # six trailing one bits

_SYNC_RE = re.compile(
    b"\x7f\xfe\x80\x01"                                       # BE16
    b"|\xfe\x7f\x01\x80"                                      # LE16
    b"|[\x1f\x5f\x9f\xdf]\xff[\x28\x68\xa8\xe8]\x00"         # BE14 (spare bits free)
    b"|\xff[\x1f\x5f\x9f\xdf]\x00[\x28\x68\xa8\xe8]"         # LE14 (spare bits free)
)

RATES = {1: 8000, 2: 16000, 3: 32000, 6: 11025, 7: 22050, 8: 44100,
         11: 12000, 12: 24000, 13: 48000}
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


def _sync_at(data, pos):
    """Layout whose sync word starts at *pos*, or None.

    Only the 14-bit payload is compared for 14-bit streams, so the spare bits in each
    16-bit word may hold any value (real DTS-in-WAV images do not always zero them).
    """
    four = data[pos:pos + 4]
    if len(four) < 4:
        return None
    lay = SYNC16.get(four)
    if lay:
        return lay
    b0, b1, b2, b3 = four
    if ((b0 << 8 | b1) & 0x3FFF, (b2 << 8 | b3) & 0x3FFF) == (SYNC14_FIRST, SYNC14_SECOND):
        return "BE14"
    if ((b1 << 8 | b0) & 0x3FFF, (b3 << 8 | b2) & 0x3FFF) == (SYNC14_FIRST, SYNC14_SECOND):
        return "LE14"
    return None


def header(data, pos, layout):
    """Parse the core frame header at *pos*, or return None if it is not a valid one."""
    probe = 16 if layout.endswith("16") else 20
    try:
        b = Bits(*_words(data, pos, layout, probe))
        if b.u(32) != SYNC_WORD:
            return None
        if layout.endswith("14") and b.u(6) != PAD14:
            return None
        ftype, short, cpf, nblks, fsize = b.u(1), b.u(5), b.u(1), b.u(7), b.u(14)
        amode, sfreq, rate = b.u(6), b.u(4), b.u(5)
        b.u(1); b.u(1); b.u(1); b.u(1); b.u(1)  # MIX DYNF TIMEF AUXF HDCD
        ext_id, ext, b_aspf, lff = b.u(3), b.u(1), b.u(1), b.u(2)
    except DtsError:
        return None
    # NBLKS and FSIZE are stored biased by one: NBLKS+1 blocks of 32 samples, and FSIZE+1
    # bytes per frame.  A core frame has at least 5 blocks and at least 96 bytes.
    if nblks < 5 or fsize < 95 or sfreq not in RATES:
        return None
    frame_bytes = fsize + 1                      # size in the 16-bit (unpacked) domain
    if layout.endswith("14"):
        # (FSIZE + 1) bytes of 8-bit data are packed 14 bits per 16-bit word.
        file_bytes = ((frame_bytes * 8 + 13) // 14) * 2
    else:
        file_bytes = frame_bytes
    return {"samples": (nblks + 1) * 32, "frame_bytes": frame_bytes, "file_bytes": file_bytes,
            "channels": CHANNELS[amode] if amode < 16 else None, "lfe": lff != 0,
            "sample_rate": RATES[sfreq], "kbps": KBPS[rate] if rate < len(KBPS) else None,
            "crc": bool(cpf), "extension": EXT.get(ext_id) if ext else None}


def scan(data):
    """Collect every complete core frame, skipping file headers and junk between frames."""
    pos, frames, layout, first = 0, [], None, None
    end = len(data)
    while pos + 4 <= end:
        match = _SYNC_RE.search(data, pos)
        if match is None:
            break
        at = match.start()
        lay = _sync_at(data, at)
        if lay is None or (layout is not None and lay != layout):
            pos = at + 1
            continue
        h = header(data, at, lay)
        if h is None or at + h["file_bytes"] > end:
            pos = at + 1                           # false sync or a frame cut off at EOF
            continue
        layout = layout or lay
        if first is None:
            first = h
        frames.append(h)
        pos = at + h["file_bytes"]
    if not frames:
        raise DtsError("no DTS core frames")
    samples = sum(f["samples"] for f in frames)
    exts = sorted({f["extension"] for f in frames if f["extension"]})
    return {"layout": layout, "frames": len(frames), "sample_rate": first["sample_rate"],
            "channels": first["channels"], "lfe": first["lfe"], "bitrate_kbps": first["kbps"],
            "samples_per_frame": first["samples"], "crc": first["crc"], "extensions": exts,
            "duration": str(Fraction(samples, first["sample_rate"])),
            "variable_frame_size": len({f["frame_bytes"] for f in frames}) > 1}
