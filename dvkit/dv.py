"""DV (IEC 61834 / SMPTE 314M) DIF streams: frames, AAUX and VAUX packs, subcode timecode."""
from fractions import Fraction

BLOCK = 80
# AAUX source pack SMP field (IEC 61834-4): 0 = 48 kHz, 1 = 44.1 kHz, 2 = 32 kHz.
RATES = {0: 48000, 1: 44100, 2: 32000}
# Samples per frame = base + AF (audio frame size from the AAUX source pack),
# keyed by (is_pal, sample_rate).
AUDIO_BASE = {
    (False, 48000): 1580, (False, 44100): 1457, (False, 32000): 1050,
    (True, 48000): 1896, (True, 44100): 1740, (True, 32000): 1260,
}
# VAUX source control pack DISP field.
ASPECT = {0: "4:3", 1: "14:9", 2: "16:9"}


class DvError(ValueError):
    pass


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _sct(block):
    return block[0] >> 5


def frame_size(data, pos):
    if pos + 6 * BLOCK > len(data) or _sct(data[pos:pos + 3]) != 0:
        raise DvError("no DIF header block at %d" % pos)
    pal = bool(data[pos + 3] & 0x80)
    seqs = 12 if pal else 10
    channel = seqs * 150 * BLOCK
    channels = 1
    nxt = pos + channel
    if nxt + 6 * BLOCK <= len(data) and _sct(data[nxt:nxt + 3]) == 0 and data[nxt + 1] & 0x08:
        channels = 2  # DV50: a second DIF channel (FSC set) completes the frame
    return pal, seqs, channel * channels, channels


def _packs(frame, sct):
    for b in range(0, len(frame), BLOCK):
        blk = frame[b:b + BLOCK]
        if _sct(blk) != sct:
            continue
        if sct == 1:
            for s in range(6):
                p = 3 + s * 8 + 3
                yield blk[p:p + 5]
        elif sct == 2:
            for s in range(15):
                yield blk[3 + s * 5:8 + s * 5]
        else:
            yield blk[3:8]


def _first(frame, sct, head):
    for p in _packs(frame, sct):
        if p[0] == head:
            return p
    return None


def _timecode(pack):
    if pack is None:
        return None
    drop = bool(pack[1] & 0x40)
    f = _bcd(pack[1] & 0x3F)
    s = _bcd(pack[2] & 0x7F)
    m = _bcd(pack[3] & 0x7F)
    h = _bcd(pack[4] & 0x3F)
    return "%02d:%02d:%02d%s%02d" % (h, m, s, ";" if drop else ":", f)


def _date(pack):
    if pack is None:
        return None
    d, m, y = _bcd(pack[2] & 0x3F), _bcd(pack[3] & 0x1F), _bcd(pack[4])
    y += 2000 if y < 69 else 1900
    return "%04d-%02d-%02d" % (y, m, d)


def _time(pack):
    if pack is None:
        return None
    return "%02d:%02d:%02d" % (_bcd(pack[4] & 0x3F), _bcd(pack[3] & 0x7F), _bcd(pack[2] & 0x7F))


def scan(data):
    pos, frames, first, last, samples = 0, 0, None, None, 0
    info = None
    while pos < len(data):
        try:
            pal, seqs, size, channels = frame_size(data, pos)
        except DvError:
            if frames:
                break
            raise
        if pos + size > len(data):
            break  # interrupted capture: the rest is trailing bytes
        frame = data[pos:pos + size]
        tc = _timecode(_first(frame, 1, 0x13))
        first = first or tc
        last = tc or last
        aas = _first(frame, 3, 0x50)
        if info is None:
            vsc = _first(frame, 2, 0x61)
            rate = RATES.get((aas[4] >> 3) & 7) if aas else None
            twelve = bool(aas[4] & 1) if aas else False
            info = {"system": "625-50" if pal else "525-60", "frame_rate": "25" if pal else "30000/1001",
                    "bitrate_mbps": 25 * channels,
                    "aspect": ASPECT.get(vsc[2] & 7) if vsc else None,
                    "audio": None if aas is None else {
                        "sample_rate": rate, "bits": 12 if twelve else 16,
                        "channels": 4 if twelve else 2,
                        "locked": not aas[1] & 0x80},
                    "recorded": None}
            rd, rt = _first(frame, 2, 0x62), _first(frame, 2, 0x63)
            if rd:
                info["recorded"] = _date(rd) + ("T" + _time(rt) if rt else "")
        if aas:
            base = AUDIO_BASE.get((pal, RATES.get((aas[4] >> 3) & 7)))
            if base is not None:
                samples += base + (aas[1] & 0x7F)
        frames += 1
        pos += size
    if info is None:
        raise DvError("no complete DV frame")
    rate = Fraction(info["frame_rate"])
    info.update(frames=frames, duration=str(frames / rate), first_timecode=first, last_timecode=last,
                audio_samples=samples if info["audio"] else None, trailing_bytes=len(data) - pos)
    return info
