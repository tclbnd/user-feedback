"""Seta X1-010 snapshots (.x1): 16 channels in PCM or waveform mode, envelopes and the PCM ROM."""

CLOCK = 16000000
HDR = 8 + 0x2000


class X1Error(ValueError):
    pass


def _s8(v):
    return v - 256 if v & 0x80 else v


def parse(data):
    if len(data) < 8 or data[:8] != b"SETAX1SN":
        raise X1Error("not an X1-010 snapshot")
    if len(data) < HDR:
        raise X1Error("chip RAM is truncated")
    ram = data[8:HDR]
    rom = data[HDR:]
    rate = CLOCK // 512
    out = []
    for ch in range(16):
        st, vol, fr, ph, s4, s5 = ram[8 * ch:8 * ch + 6]
        if not st & 1:
            continue
        c = {"channel": ch + 1, "mode": "pcm" if st & 2 else "wave"}
        if not st & 2:
            pitch = (ph << 8) | fr
            wave = [_s8(b) for b in ram[0x1000 + vol * 256:0x1000 + vol * 256 + 128]]
            env = ram[0x80 + s5 * 128:0x80 + s5 * 128 + 128]
            c.update(waveform=vol, wave_address=0x1000 + vol * 256, cycle_hz="%.3f" % (rate * pitch / 1024),
                     envelope=s5, env_address=0x80 + s5 * 128, once=not st & 4,
                     env_hz="%.3f" % (rate * s4 / 1024 / 128), left=env[0] & 15, right=env[0] >> 4,
                     wave_peak=max(abs(v) for v in wave), wave_sum=sum(wave))
        else:
            start, end = s4 << 12, s5 << 12
            freq = fr
            vals, ok = [], True
            for a in range(start, end):
                if a >= len(rom):
                    ok = False
                    break
                vals.append(rom[a] - 0x80)
            c.update(start=start, end=end, left=vol & 15, right=vol >> 4, rate_hz="%.2f" % (rate * freq / 16),
                     length=len(vals), peak=max((abs(v) for v in vals), default=0), sum=sum(vals), complete=ok)
        out.append(c)
    return {"output_rate": rate, "channels_on": len(out), "channels": out}
