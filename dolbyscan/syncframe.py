"""Sync frame headers (bit stream information)."""
from .bits import Bits

RATES = [48000, 44100, 32000]
KBPS = [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640]
CHANNELS = [2, 1, 2, 3, 3, 4, 4, 5]


class SyncError(ValueError):
    pass


def frame_words(fscod, frmsizecod):
    kbps = KBPS[frmsizecod >> 1]
    return kbps * 2 if fscod != 2 else kbps * 3


def parse(data, pos):
    if data[pos:pos + 2] != b"\x0b\x77":
        raise SyncError(f"no sync word at {pos}")
    b = Bits(data, pos + 2)
    b.u(16)
    fscod = b.u(2)
    frmsizecod = b.u(6)
    bsid = b.u(5)
    b.u(3)
    acmod = b.u(3)
    lfe = b.u(1)
    dialnorm = b.u(5)
    if fscod == 3 or frmsizecod > 37:
        raise SyncError("reserved sample rate or frame size")
    return {"codec": "ac3", "bsid": bsid, "sample_rate": RATES[fscod], "acmod": acmod, "lfe": bool(lfe),
            "channels": CHANNELS[acmod] + lfe, "bitrate": KBPS[frmsizecod >> 1] * 1000,
            "dialnorm": -dialnorm, "size": frame_words(fscod, frmsizecod) * 2, "samples": 1536}
