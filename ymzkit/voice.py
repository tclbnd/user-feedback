"""YMZ280B snapshots (.ymz): key-on voices, their pitch, panning and the samples they play once from start to stop."""

CLOCK = 16934400
INDEX_SCALE = (0x0E6, 0x0E6, 0x0E6, 0x0E6, 0x133, 0x199, 0x200, 0x266)
FORMATS = {1: "adpcm4", 2: "pcm8", 3: "pcm16"}


class YmzError(ValueError):
    pass


def _decode(fmt, rom, start, stop):
    rb = lambda a: rom[a] if a < len(rom) else 0
    out = []
    pos = start
    signal, step = 0, 0x7F
    while pos < stop:
        if fmt == 1:
            val = rb(pos // 2) >> ((pos & 1) << 2) & 15
            d = (val & 7) * 2 + 1
            signal += step * (-d if val & 8 else d) // 8
            signal = max(-32768, min(32767, signal))
            step = (step * INDEX_SCALE[val & 7]) >> 8
            out.append(signal)
            pos += 1
        elif fmt == 2:
            b = rb(pos // 2)
            out.append((b - 256 if b & 0x80 else b) * 256)
            pos += 2
        else:
            w = rb(pos // 2) << 8 | rb(pos // 2 + 1)
            out.append(w - 65536 if w & 0x8000 else w)
            pos += 4
    return out


def parse(data):
    if len(data) < 8 or data[:8] != b"YMZ280B\0":
        raise YmzError("not a YMZ280B snapshot")
    if len(data) < 8 + 256 + 4:
        raise YmzError("register file is truncated")
    regs = data[8:264]
    n = int.from_bytes(data[264:268], "little")
    rom = data[268:268 + n]
    if len(rom) < n:
        raise YmzError("sample ROM is truncated")
    enable = True
    voices = []
    for v in range(8):
        r1 = regs[0x01 + 4 * v]
        mode = r1 >> 5 & 3
        if not r1 & 0x80:
            continue
        fnum = (r1 & 1) << 8 | regs[0x00 + 4 * v]
        level, pan = regs[0x02 + 4 * v], regs[0x03 + 4 * v] & 15
        if pan <= 8:
            left, right = level, level * pan // 8
        else:
            left, right = level * (16 - pan) // 8, level
        addr = [regs[0x20 + 4 * v + k] << 16 | regs[0x40 + 4 * v + k] << 8 | regs[0x60 + 4 * v + k] for k in range(4)]
        smp = _decode(mode, rom, addr[0] * 2, addr[3] * 2) or [0]
        voices.append({"voice": v + 1, "format": FORMATS.get(mode), "playing": enable, "looping": bool(r1 & 0x10),
                       "fnum": fnum, "rate_hz": "%.1f" % ((fnum + 1) * CLOCK / 384 / 256), "level": level,
                       "left": left, "right": right, "start": addr[0], "loop_start": addr[1],
                       "loop_end": addr[2], "stop": addr[3], "samples": len(smp),
                       "head": smp[:8], "peak": max(abs(s) for s in smp), "sum": sum(smp)})
    return {"key_on_enable": enable, "rom_bytes": n, "voices": voices}
