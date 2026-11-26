"""Atari POKEY register dumps (.pkr): header, per-frame AUDF/AUDC/AUDCTL, channel frequencies."""
import struct

CLOCK = {"NTSC": 1789772.5, "PAL": 1773447.0}
RATE = {"NTSC": 60, "PAL": 50}


class DumpError(ValueError):
    pass


def parse(data):
    if len(data) < 14 or data[:8] != b"POKEYREG":
        raise DumpError("not a POKEY register dump")
    system = "PAL" if data[8] & 1 else "NTSC"
    n = struct.unpack_from("<I", data, 10)[0]
    body = data[14:]
    have = min(n, len(body) // 9)
    clock = CLOCK[system]
    rate = RATE[system]
    chans = [{"sounding_frames": 0, "pure_frames": 0, "volume_only_frames": 0, "filtered_frames": 0,
              "first_freq": None, "lowest_freq": None, "highest_freq": None} for _ in range(4)]
    for i in range(have):
        r = body[9 * i:9 * i + 9]
        audf, audc, ctl = r[0:8:2], r[1:8:2], r[8]
        base = clock / 28
        fast = [bool(ctl & 0x20), False, bool(ctl & 0x40), False]
        freq = [None] * 4
        for ch in range(4):
            if fast[ch]:
                freq[ch] = clock / (2 * (audf[ch] + 1))
            else:
                freq[ch] = base / (2 * (audf[ch] + 1))
        for ch in range(4):
            vol = audc[ch] & 15
            if not vol:
                continue
            c = chans[ch]
            c["sounding_frames"] += 1
            if ch == 0 and ctl & 0x02 or ch == 1 and ctl & 0x04:
                c["filtered_frames"] += 1
            if audc[ch] & 0x10:
                c["volume_only_frames"] += 1
            if audc[ch] & 0xF0 == 0xA0:
                c["pure_frames"] += 1
            f = freq[ch]
            if c["first_freq"] is None:
                c["first_freq"] = f
            c["lowest_freq"] = f if c["lowest_freq"] is None else min(c["lowest_freq"], f)
            c["highest_freq"] = f if c["highest_freq"] is None else max(c["highest_freq"], f)
    for c in chans:
        for k in ("first_freq", "lowest_freq", "highest_freq"):
            if c[k] is not None:
                c[k] = "%.3f" % c[k]
    return {"system": system, "frames": have, "complete": have == n,
            "frame_rate": "%.3f" % rate, "duration": "%.3f" % (have / rate), "channels": chans}
