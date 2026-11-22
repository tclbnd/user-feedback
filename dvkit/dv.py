"""DV (IEC 61834 / SMPTE 314M) DIF streams: frames, AAUX and VAUX packs, subcode timecode."""
from fractions import Fraction

BLOCK = 80
RATES = {0: 48000, 1: 32000, 2: 44100}
SAMPLES_PER_FRAME = {48000: 1602, 44100: 1471, 32000: 1068}
ASPECT = {0: "4:3", 2: "16:9"}


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
    seqs = 10
    return pal, seqs, seqs * 150 * BLOCK, 1


def _packs(frame, sct, first, count):
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
    for p in _packs(frame, sct, 0, 0):
        if p[0] == head:
            return p
    return None


def _timecode(pack, pal):
    if pack is None:
        return None
    f, s, m, h = pack[1] & 0x3F, pack[2] & 0x7F, pack[3] & 0x7F, pack[4] & 0x3F
    return "%02d:%02d:%02d:%02d" % (h, m, s, f)


def _date(pack):
    if pack is None:
        return None
    d, m, y = _bcd(pack[2] & 0x3F), _bcd(pack[3] & 0x1F), _bcd(pack[4])
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
            raise DvError("frame at %d runs past the end of the file" % pos)
        frame = data[pos:pos + size]
        tc = _timecode(_first(frame, 1, 0x13), pal)
        first = first or tc
        last = tc or last
        aas = _first(frame, 3, 0x50)
        if info is None:
            vsc = _first(frame, 2, 0x61)
            rate = RATES.get((aas[4] >> 3) & 7) if aas else None
            info = {"system": "625-50" if pal else "525-60", "frame_rate": "25" if pal else "30000/1001",
                    "bitrate_mbps": 25 * channels,
                    "aspect": ASPECT.get(vsc[3] & 7) if vsc else None,
                    "audio": None if aas is None else {
                        "sample_rate": rate, "bits": 16, "channels": 2,
                        "locked": not aas[1] & 0x80},
                    "recorded": None}
            rd, rt = _first(frame, 2, 0x62), _first(frame, 2, 0x63)
            if rd:
                info["recorded"] = _date(rd) + ("T" + _time(rt) if rt else "")
        if aas:
            rate = RATES.get((aas[4] >> 3) & 7)
            if rate:
                samples += SAMPLES_PER_FRAME[rate]
        frames += 1
        pos += size
    if info is None:
        raise DvError("no complete DV frame")
    rate = Fraction(info["frame_rate"])
    info.update(frames=frames, duration=str(frames / rate), first_timecode=first, last_timecode=last,
                audio_samples=samples if info["audio"] else None, trailing_bytes=len(data) - pos)
    return info
