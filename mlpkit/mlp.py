"""Dolby TrueHD / Meridian MLP streams: access units, major sync info, AC-3 core frames."""
import struct
from fractions import Fraction

SYNC_THD, SYNC_MLP = 0xF8726FBA, 0xF8726FBB
QUANT = {0: 16, 1: 20, 2: 24}
MLP_CHANNELS = [1, 2, 3, 4, 3, 4, 5, 3, 4, 5, 4, 5, 6, 4, 5, 4, 5, 6, 5, 5, 6]
# TrueHD channel assignment bits: L/R, C, LFE, Ls/Rs, Lvh/Rvh, Lc/Rc, Lrs/Rrs, Cs, Ts, Lsd/Rsd, Lw/Rw, Cvh, LFE2
THD_BITS = [2, 1, 1, 2, 2, 2, 2, 1, 1, 2, 2, 1, 1]
class MlpError(ValueError):
    pass


def rate_of(bits):
    if bits == 0xF:
        return None
    return 48000 << (bits & 7)


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
        out.update(rate_bits=r1, sample_rate=rate_of(r1), bits=[24, 0], rate2=None,
                   channels=thd_channels(arr1), channels_6ch=thd_channels(arr1))
    if struct.unpack_from(">H", data, p + 8)[0] != 0xB752:
        raise MlpError("major sync without the 0xB752 signature")
    vp = struct.unpack_from(">H", data, p + 14)[0]
    out["vbr"] = bool(vp >> 15)
    out["peak_bitrate"] = ((vp & 0x7FFF) * out["sample_rate"]) >> 4 if out["sample_rate"] else None
    out["substreams"] = data[p + 16] >> 4
    return out


def parse(data):
    p, n = 0, len(data)
    info = None
    units = syncs = ac3 = skipped = 0
    while p + 4 <= n:
        if info is None:
            if p + 8 <= n and struct.unpack_from(">I", data, p + 4)[0] == SYNC_THD:
                info = major(data, p + 4)
            else:
                p += 1
                skipped += 1
                continue
        length = struct.unpack_from(">H", data, p)[0] & 0x0FFF
        if length < 4 or p + length > n:
            break
        if p + 8 <= n and struct.unpack_from(">I", data, p + 4)[0] == SYNC_THD:
            syncs += 1
        units += 1
        p += length
    if info is None:
        raise MlpError("no major sync found")
    per_unit = 40
    out = {k: v for k, v in info.items() if k != "rate_bits"}
    out.update(access_units=units, major_syncs=syncs, ac3_frames=ac3, skipped_bytes=skipped,
               samples=units * per_unit,
               duration=str(Fraction(units * per_unit, info["sample_rate"])) if info["sample_rate"] else None)
    return out
