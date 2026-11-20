"""Sync frame headers (bit stream information), ATSC A/52 AC-3 and Annex E E-AC-3."""
from .bits import Bits

RATES = [48000, 44100, 32000]
RATES_REDUCED = [24000, 22050, 16000]  # E-AC-3 with fscod == 3, indexed by fscod2
KBPS = [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640]
CHANNELS = [2, 1, 2, 3, 3, 4, 4, 5]  # full-bandwidth channels by acmod, without LFE
BLOCKS = [1, 2, 3, 6]  # audio blocks per sync frame by numblkscod


class SyncError(ValueError):
    pass


def frame_words(fscod, frmsizecod):
    """AC-3 sync frame length in 16-bit words (A/52 table 5.18).

    At 44.1 kHz the frame size alternates between two values one word
    apart; the value selected by frmsizecod is returned here, and parse()
    offers both candidates of the pair via the "sizes" list.
    """
    kbps = KBPS[frmsizecod >> 1]
    if fscod == 0:  # 48 kHz
        return 2 * kbps
    if fscod == 2:  # 32 kHz
        return 3 * kbps
    # 44.1 kHz: 2 * kbps * 48000 / 44100 words, split into a pair of sizes
    return (kbps * 96000) // 44100 + (frmsizecod & 1)


def _ac3(data, pos, bsid):
    b = Bits(data, pos + 2)
    b.u(16)  # crc1
    fscod = b.u(2)
    frmsizecod = b.u(6)
    if fscod == 3 or frmsizecod > 37:
        raise SyncError("reserved sample rate or frame size")
    b.u(5)  # bsid, already checked
    b.u(3)  # bsmod
    acmod = b.u(3)
    if acmod & 0x1 and acmod != 0x1:  # three front channels
        b.u(2)  # cmixlev
    if acmod & 0x4:  # surround channels present
        b.u(2)  # surmixlev
    if acmod == 0x2:  # 2/0 mode
        b.u(2)  # dsurmod
    lfe = b.u(1)
    dialnorm = b.u(5)
    words = frame_words(fscod, frmsizecod)
    frame = {"codec": "ac3", "bsid": bsid, "sample_rate": RATES[fscod],
             "acmod": acmod, "lfe": bool(lfe), "channels": CHANNELS[acmod] + lfe,
             "bitrate": KBPS[frmsizecod >> 1] * 1000, "dialnorm": -dialnorm,
             "size": words * 2, "samples": 1536}
    if fscod == 1:
        alt = words - 1 if frmsizecod & 1 else words + 1
        frame["sizes"] = [words * 2, alt * 2]  # alternating pair, primary first
    return frame


def _eac3(data, pos, bsid):
    b = Bits(data, pos + 2)
    strmtyp = b.u(2)  # 0/2: independent substream, 1: dependent substream
    substreamid = b.u(3)
    frmsiz = b.u(11)
    fscod = b.u(2)
    if fscod == 3:  # reduced sample rate, 6 blocks per frame
        fscod2 = b.u(2)
        if fscod2 == 3:
            raise SyncError("reserved sample rate")
        rate, numblkscod = RATES_REDUCED[fscod2], 3
    else:
        rate, numblkscod = RATES[fscod], b.u(2)
    acmod = b.u(3)
    lfe = b.u(1)
    b.u(5)  # bsid, already checked
    dialnorm = b.u(5)
    samples = BLOCKS[numblkscod] * 256
    size = (frmsiz + 1) * 2
    return {"codec": "eac3", "bsid": bsid, "strmtyp": strmtyp, "substreamid": substreamid,
            "sample_rate": rate, "acmod": acmod, "lfe": bool(lfe),
            "channels": CHANNELS[acmod] + lfe, "bitrate": size * 8 * rate // samples,
            "dialnorm": -dialnorm, "size": size, "samples": samples,
            "blocks": BLOCKS[numblkscod]}


def parse(data, pos):
    if data[pos:pos + 2] != b"\x0b\x77":
        raise SyncError(f"no sync word at {pos}")
    bsid = data[pos + 5] >> 3  # bits 40-44 of the frame in both AC-3 and E-AC-3
    if bsid <= 8:
        return _ac3(data, pos, bsid)
    if 11 <= bsid <= 16:
        return _eac3(data, pos, bsid)
    raise SyncError(f"reserved bsid {bsid} at {pos}")
