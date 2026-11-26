"""Nintendo DS SWAV sounds (.swav): file header, DATA block, PCM8/PCM16/IMA-ADPCM waves."""
import struct
from fractions import Fraction

KINDS = {0: "pcm8", 1: "pcm16", 2: "adpcm"}

# DS sound hardware timer clock (Hz)
DS_CLOCK = 16756991

# IMA-ADPCM (DVI/Dialogic) tables used by the DS sound hardware
_ADPCM_INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)
_ADPCM_STEP = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    875, 962, 1058, 1163, 1279, 1407, 1548, 1703, 1873, 2060,
    2266, 2492, 2741, 3015, 3316, 3648, 4013, 4414, 4855, 5341,
    5875, 6462, 7108, 7819, 8601, 9461, 10407, 11447, 12592, 13851,
    15236, 16760, 18436, 20279, 22307, 24538, 26992, 29691, 32767,
)


class SwavError(ValueError):
    pass


def _signed8(v):
    return v - 256 if v >= 128 else v


def _decode_adpcm(payload):
    """Decode DS IMA-ADPCM. First word is the 4-byte header (one sample);
    every following word is one block: header byte + six 4-bit codes."""
    vals = []
    if len(payload) < 4:
        return vals
    index = min(payload[0], 88)
    predictor = struct.unpack_from("<h", payload, 2)[0]
    vals.append(predictor)
    step = _ADPCM_STEP[index]
    pos = 4
    while pos + 4 <= len(payload):
        # Block header: upper nibble reloads the step index for this block
        index = payload[pos] >> 4
        step = _ADPCM_STEP[index]
        d1, d2, d3 = payload[pos + 1], payload[pos + 2], payload[pos + 3]
        codes = (d1 >> 4, d1 & 0xF, d2 >> 4, d2 & 0xF, d3 >> 4, d3 & 0xF)
        for code in codes:
            diff = step >> 3
            if code & 1:
                diff += step
            if code & 2:
                diff += step >> 1
            if code & 4:
                diff += step >> 2
            if code & 8:
                predictor -= diff
            else:
                predictor += diff
            if predictor > 32767:
                predictor = 32767
            elif predictor < -32768:
                predictor = -32768
            vals.append(predictor)
            index += _ADPCM_INDEX[code & 7]
            index = max(0, min(88, index))
            step = _ADPCM_STEP[index]
        pos += 4
    return vals


def parse(data):
    if len(data) < 16 + 8 + 12 or data[:4] != b"SWAV":
        raise SwavError("not a SWAV file")
    b = 16
    if data[b:b + 4] != b"DATA":
        raise SwavError("missing DATA block")
    kind, loop, rate, timer, loff, nlen = struct.unpack_from("<BBHHHI", data, b + 8)
    if kind not in KINDS:
        raise SwavError("unsupported wave type %d" % kind)

    payload = data[b + 20:]

    # Offset/length units follow the DS sound channel registers:
    # PCM8 = bytes (one sample each), PCM16 = halfwords, ADPCM = words (4 bytes).
    if kind == 0:
        units = loff + nlen
        nbytes = units
        total, lstart = units, loff
        wave = payload[:nbytes]
        # PCM8 is signed; scale to the 16-bit range with v * 256
        vals = [_signed8(v) << 8 for v in wave]
    elif kind == 1:
        units = loff + nlen
        nbytes = units * 2
        total, lstart = units, loff
        wave = payload[:nbytes]
        vals = [struct.unpack_from("<h", wave, 2 * i)[0] for i in range(len(wave) // 2)]
    else:
        words = loff + nlen
        nbytes = words * 4
        # Header word holds the first sample; each later word decodes six.
        total = 1 + 6 * (words - 1) if words else 0
        # The loop points at a block word; its first decoded sample is 6*w - 5.
        lstart = 6 * loff - 5 if loff else 0
        vals = _decode_adpcm(payload[:nbytes])

    complete = len(payload) >= nbytes
    period = timer + 1
    timer_rate = (2 * DS_CLOCK + period) // (2 * period)  # rounded to whole Hz
    peak = max((abs(v) for v in vals), default=0)
    return {"type": KINDS[kind], "looped": bool(loop), "sample_rate": rate, "timer": timer,
            "timer_rate": timer_rate, "samples": total,
            "loop_start": lstart if loop else None,
            "duration": str(Fraction(total, rate)) if rate else None,
            "complete": complete, "peak": "%.6f" % (peak / 32768),
            "last_sample": vals[-1] if vals else None}
