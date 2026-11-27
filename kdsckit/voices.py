"""Konami K053260 snapshots (.kdsc): four voices, PCM and KADPCM samples, reverse play, pan and pitch."""

CLOCK = 3579545
HDR = 8 + 48
KADPCM = (0, 1, 2, 4, 8, 16, 32, 64, -128, -64, -32, -16, -8, -4, -2, -1)


class KdscError(ValueError):
    pass


def _s8(v):
    v &= 0xFF
    return v - 256 if v & 0x80 else v


def parse(data):
    if len(data) < 8 or data[:8] != b"K053260S":
        raise KdscError("not a K053260 snapshot")
    if len(data) < HDR:
        raise KdscError("register block is truncated")
    r = data[8:HDR]
    rom = data[HDR:]
    voices = []
    for i in range(4):
        b = 8 + 8 * i
        pitch = r[b] | (r[b + 1] & 0x0F) << 8
        length = r[b + 2] | r[b + 3] << 8
        start = r[b + 4] | r[b + 5] << 8
        vol = r[b + 7] & 0x7F
        pan = (r[0x2C + i // 2] >> (3 * (i & 1))) & 7
        on, rev = bool(r[0x28] >> i & 1), bool(r[0x28] >> (i + 4) & 1)
        loop, adp = bool(r[0x2A] >> i & 1), bool(r[0x2A] >> (i + 4) & 1)
        v = {"voice": i + 1, "on": on, "start": start, "length": length, "rate_hz": "%.2f" % (CLOCK / 64 * pitch / 4096),
             "volume": vol, "left": vol * (7 - pan) * 127 // 7, "right": vol * pan * 127 // 7,
             "loop": loop, "reverse": rev, "format": "kadpcm" if adp else "pcm",
             "samples": None, "peak": None, "sum": None, "last": None, "complete": None}
        if on:
            outs, acc, ok = [], 0, True
            for k in range(length):
                a = start + k
                if not 0 <= a < len(rom):
                    ok = False
                    break
                byte = rom[a]
                if adp:
                    for nib in (byte >> 4, byte & 15):
                        acc = max(-128, min(127, acc + KADPCM[nib]))
                        outs.append(acc)
                else:
                    outs.append(_s8(byte))
            v.update(samples=len(outs), peak=max((abs(x) for x in outs), default=0), sum=sum(outs),
                     last=outs[-1] if outs else None, complete=ok)
        voices.append(v)
    return {"rom_size": len(rom), "voices_on": sum(v["on"] for v in voices), "voices": voices}
