"""Dolby TrueHD / Meridian MLP streams: access units, major sync info, AC-3 core frames."""
import struct
from fractions import Fraction

SYNC_THD, SYNC_MLP = 0xF8726FBA, 0xF8726FBB
SYNCS = (SYNC_THD, SYNC_MLP)
QUANT = {0: 16, 1: 20, 2: 24}
MLP_CHANNELS = [1, 2, 3, 4, 3, 4, 5, 3, 4, 5, 4, 5, 6, 4, 5, 4, 5, 6, 5, 5, 6]
# TrueHD channel assignment bits: L/R, C, LFE, Ls/Rs, Lvh/Rvh, Lc/Rc, Lrs/Rrs, Cs, Ts, Lsd/Rsd, Lw/Rw, Cvh, LFE2
THD_BITS = [2, 1, 1, 2, 2, 2, 2, 1, 1, 2, 2, 1, 1]
AC3_SYNC = 0x0B77
# AC-3 bit rate in kbit/s, indexed by frmsizecod >> 1
AC3_BITRATE = [32, 40, 48, 56, 64, 80, 96, 112, 128, 160,
               192, 224, 256, 320, 384, 448, 512, 576, 640]
# AC-3 frame length in 16-bit words, indexed by [frmsizecod][fscod]
# (fscod: 0 = 48 kHz, 1 = 44.1 kHz, 2 = 32 kHz)
AC3_WORDS = [
    (64, 69, 96), (64, 70, 96), (80, 87, 120), (80, 88, 120),
    (96, 104, 144), (96, 105, 144), (112, 121, 168), (112, 122, 168),
    (128, 139, 192), (128, 140, 192), (160, 174, 240), (160, 175, 240),
    (192, 208, 288), (192, 209, 288), (224, 243, 336), (224, 244, 336),
    (256, 278, 384), (256, 279, 384), (320, 348, 480), (320, 349, 480),
    (384, 417, 576), (384, 418, 576), (448, 487, 672), (448, 488, 672),
    (512, 557, 768), (512, 558, 768), (640, 696, 960), (640, 697, 960),
    (768, 835, 1152), (768, 836, 1152), (896, 975, 1344), (896, 976, 1344),
    (1024, 1114, 1536), (1024, 1115, 1536), (1152, 1253, 1728), (1152, 1254, 1728),
    (1280, 1393, 1920), (1280, 1394, 1920),
]


class MlpError(ValueError):
    pass


def rate_of(bits):
    if bits == 0xF:
        return None
    # bit 3 selects the 44.1 kHz family, the low 3 bits the multiplier
    return (44100 if bits & 8 else 48000) << (bits & 7)


def thd_channels(arrangement):
    return sum(n for i, n in enumerate(THD_BITS) if arrangement >> i & 1)


def major(data, p):
    word, info = struct.unpack_from(">II", data, p)
    out = {"format": "TrueHD" if word == SYNC_THD else "MLP"}
    if word == SYNC_MLP:
        g1, g2, r1, r2 = info >> 28, (info >> 24) & 15, (info >> 20) & 15, (info >> 16) & 15
        arr = info & 0x1F
        out.update(rate_bits=r1, sample_rate=rate_of(r1), bits=[QUANT.get(g1, 0), QUANT.get(g2, 0)],
                   rate2=rate_of(r2), channels=MLP_CHANNELS[arr] if arr < len(MLP_CHANNELS) else None, channels_6ch=None)
    else:
        r1 = info >> 28
        arr1, arr2 = (info >> 15) & 0x1F, info & 0x1FFF
        ch6, ch8 = thd_channels(arr1), thd_channels(arr2)
        out.update(rate_bits=r1, sample_rate=rate_of(r1), bits=[24, 0], rate2=None,
                   channels=ch8 or ch6, channels_6ch=ch6)
    if struct.unpack_from(">H", data, p + 8)[0] != 0xB752:
        raise MlpError("major sync without the 0xB752 signature")
    vp = struct.unpack_from(">H", data, p + 14)[0]
    out["vbr"] = bool(vp >> 15)
    out["peak_bitrate"] = ((vp & 0x7FFF) * out["sample_rate"]) >> 4 if out["sample_rate"] else None
    out["substreams"] = data[p + 16] >> 4
    return out


def description(info):
    """The stream-defining fields of a major sync, for change detection."""
    return (info["format"], info["sample_rate"], info["rate2"], tuple(info["bits"]),
            info["channels"], info["channels_6ch"], info["substreams"])


def ac3_frame(data, p):
    """(length in bytes, kbit/s) of the AC-3 core frame at p, or None."""
    if p + 5 > len(data) or struct.unpack_from(">H", data, p)[0] != AC3_SYNC:
        return None
    fscod, sizecod = data[p + 4] >> 6, data[p + 4] & 0x3F
    if fscod == 3 or sizecod >= len(AC3_WORDS):
        return None
    return AC3_WORDS[sizecod][fscod] * 2, AC3_BITRATE[sizecod >> 1]


def parse(data):
    p, n = 0, len(data)
    info = first = None
    rate_bits = 0
    units = syncs = ac3 = ac3_bytes = skipped = changes = largest = samples = 0
    ac3_kbps = None
    while p + 4 <= n:
        if info is None:
            if p + 8 <= n and struct.unpack_from(">I", data, p + 4)[0] in SYNCS:
                info = major(data, p + 4)
                first = description(info)
                rate_bits = info["rate_bits"]
            else:
                p += 1
                skipped += 1
                continue
        frame = ac3_frame(data, p)
        if frame is not None:
            size, kbps = frame
            if p + size > n:
                break
            ac3 += 1
            ac3_bytes += size
            if ac3_kbps is None:
                ac3_kbps = kbps
            p += size
            continue
        # access unit header: 4-bit check nibble, then the length in 16-bit words
        size = (struct.unpack_from(">H", data, p)[0] & 0x0FFF) * 2
        if size < 4 or p + size > n:
            break
        if p + 8 <= n and struct.unpack_from(">I", data, p + 4)[0] in SYNCS:
            syncs += 1
            cur = major(data, p + 4)
            rate_bits = cur["rate_bits"]
            if description(cur) != first:
                changes += 1
        units += 1
        samples += 40 << (rate_bits & 7)
        largest = max(largest, size)
        p += size
    if info is None:
        raise MlpError("no major sync found")
    out = {k: v for k, v in info.items() if k != "rate_bits"}
    out.update(access_units=units, major_syncs=syncs, ac3_frames=ac3,
               ac3_bytes=ac3_bytes, ac3_kbps=ac3_kbps, format_changes=changes,
               largest_unit=largest, skipped_bytes=skipped, samples=samples,
               duration=str(Fraction(samples, info["sample_rate"])) if info["sample_rate"] else None)
    return out
