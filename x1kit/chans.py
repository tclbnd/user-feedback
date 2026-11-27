"""Seta X1-010 snapshots (.x1): 16 channels in PCM or waveform mode, envelopes and the PCM ROM."""

CLOCK = 16000000
HDR = 8 + 0x2000

WAVE_BASE = 0x1000  # waveform tables: 128 signed bytes each, from $1000
ENV_LEN = 128       # envelope tables: 128 bytes each, from $0000
WAVE_LEN = 128


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
        c = {"channel": ch + 1, "mode": "wave" if st & 2 else "pcm"}
        if st & 2:
            pitch = (ph << 8) | fr
            wave = [_s8(b) for b in ram[WAVE_BASE + vol * WAVE_LEN:WAVE_BASE + (vol + 1) * WAVE_LEN]]
            env = ram[s5 * ENV_LEN:(s5 + 1) * ENV_LEN]
            c.update(waveform=vol, wave_address=WAVE_BASE + vol * WAVE_LEN, cycle_hz="%.3f" % (rate * pitch / 1024 / WAVE_LEN),
                     envelope=s5, env_address=s5 * ENV_LEN, once=bool(st & 4),
                     env_hz="%.3f" % (rate * s4 / 1024 / ENV_LEN), left=env[0] >> 4, right=env[0] & 15,
                     wave_peak=max(abs(v) for v in wave), wave_sum=sum(wave))
        else:
            start, end = s4 << 12, (0x100 - s5) << 12
            freq = fr & 0x7F
            if freq == 0:
                freq = 128
            div = 256 if fr & 0x80 else 16
            vals, ok = [], True
            for a in range(start, end):
                if a >= len(rom):
                    ok = False
                    break
                vals.append(_s8(rom[a]))
            c.update(start=start, end=end, left=vol >> 4, right=vol & 15, rate_hz="%.2f" % (rate * freq / div),
                     length=len(vals), peak=max((abs(v) for v in vals), default=0), sum=sum(vals), complete=ok)
        out.append(c)
    return {"output_rate": rate, "channels_on": len(out), "channels": out}
