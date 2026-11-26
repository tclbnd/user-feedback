"""CD-XA ADPCM audio (.xa): 2336-byte Mode 2 Form 2 sectors, sound groups, sound units.

A sector starts with an 8-byte subheader (file number, channel, submode and
coding information, each written twice).  ADPCM audio lives in Mode 2 Form 2
sectors (the submode's Form 2 bit); Form 1 data sectors interleaved between
the streams carry no sound and are skipped.

An audio sector holds 18 sound groups of 128 bytes.  Each group starts with a
16-byte header: one parameter byte per sound unit (two copies).  4-bit ADPCM
groups have eight units of 28 samples packed into nibbles, 8-bit groups have
four units whose samples are whole bytes.  In stereo the even units are the
left channel and the odd units the right one; every unit keeps its own
predictor history.
"""
from fractions import Fraction

# (a0, a1) predictor coefficients for the four filter selectors.
K = [(0, 0), (60, 0), (115, -52), (98, -55)]
SECTOR = 2336
GROUPS = 18
GROUP = 128
UNIT_SAMPLES = 28

# Submode bits.  CD-XA ADPCM audio is carried in Mode 2 Form 2 sectors; the
# Form 2 bit is therefore the marker that separates audio sectors from the
# Form 1 data sectors interleaved between the streams.
SUB_FORM2 = 0x20

# Coding information bits.
CODE_STEREO = 0x01
CODE_RATE_18900 = 0x04
CODE_8BIT = 0x10


class XaError(ValueError):
    pass


def _decode_group(grp, bits, stereo, hist, sink):
    """Decode one 128-byte sound group, recording every sample via sink."""
    units = 4 if bits == 8 else 8
    for u in range(units):
        # The header is duplicated; grp[u] and grp[u + 8] are identical.
        p = grp[u]
        shift = p & 0x0F
        filt = (p >> 4) & 0x03
        f0, f1 = K[filt]
        h = hist[u]
        for i in range(UNIT_SAMPLES):
            if bits == 8:
                n = grp[16 + 4 * i + u]
                v = n - 256 if n & 0x80 else n
            else:
                # Two units share a byte: even unit in the high nibble.
                n = (grp[16 + 4 * i + u // 2] >> (4 * (1 - (u & 1)))) & 0x0F
                v = n - 16 if n & 0x08 else n
            d = (v << 12) >> shift
            y = d + ((h[0] * f0 + h[1] * f1 + 32) >> 6)
            y = max(-32768, min(32767, y))
            h[1], h[0] = h[0], y
            channel = (u & 1) if stereo else 0
            sink(channel, y)


def parse(data):
    if len(data) < SECTOR:
        raise XaError("not an XA file")
    complete = len(data) % SECTOR == 0
    order = []
    streams = {}
    for k in range(len(data) // SECTOR):
        s = data[k * SECTOR:(k + 1) * SECTOR]
        filenum, channel, submode, code = s[0], s[1], s[2], s[3]
        # Data sectors (and anything without the Form 2 audio marker) are skipped.
        if not (submode & SUB_FORM2):
            continue
        key = "%d/%d" % (filenum, channel)
        if key not in streams:
            stereo = bool(code & CODE_STEREO)
            bits = 8 if code & CODE_8BIT else 4
            rate = 18900 if code & CODE_RATE_18900 else 37800
            streams[key] = {
                "sectors": 0, "stereo": stereo, "bits": bits, "rate": rate,
                # One independent history per sound unit (0..7).
                "hist": [[0, 0] for _ in range(8)],
                "count": 0, "peak": 0, "last_left": None, "last_right": None,
            }
            order.append(key)
        st = streams[key]
        st["sectors"] += 1
        hist = st["hist"]

        def sink(ch, y, st=st):
            st["count"] += 1
            if abs(y) > st["peak"]:
                st["peak"] = abs(y)
            if ch == 0:
                st["last_left"] = y
            else:
                st["last_right"] = y

        for g in range(GROUPS):
            grp = s[8 + GROUP * g:8 + GROUP * (g + 1)]
            _decode_group(grp, st["bits"], st["stereo"], hist, sink)

    out = []
    for key in order:
        st = streams[key]
        n = st["count"]
        out.append({
            "stream": key,
            "sectors": st["sectors"],
            "stereo": st["stereo"],
            "sample_rate": st["rate"],
            "bits": st["bits"],
            "samples": n,
            "duration": str(Fraction(n, st["rate"])),
            "peak": "%.6f" % (st["peak"] / 32768),
            "last_left": st["last_left"],
            "last_right": st["last_right"] if st["stereo"] else None,
        })
    return {"sectors": len(data) // SECTOR, "complete": complete, "streams": out}
