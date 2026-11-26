"""Atari POKEY register dumps (.pkr): header, per-frame AUDF/AUDC/AUDCTL, channel frequencies."""
import struct

CLOCK = {"NTSC": 1789772.5, "PAL": 1773447.0}
# Vertical blanks per second: one frame is 262*114 NTSC / 312*114 PAL
# machine-clock cycles.
RATE = {"NTSC": CLOCK["NTSC"] / (262 * 114), "PAL": CLOCK["PAL"] / (312 * 114)}

# AUDCTL bits
CTL_15KHZ = 0x01       # timers 1 & 2 clocked from the 15 kHz (master/114) timer
CTL_HIPASS2 = 0x02     # channel 2 high-pass filter driven by channel 1
CTL_HIPASS3 = 0x04     # channel 3 high-pass filter driven by channel 2
CTL_HIPASS4 = 0x08     # channel 4 high-pass filter driven by channel 4
CTL_FAST34 = 0x10      # timers 3 & 4 clocked from the 1.79 MHz master clock
CTL_FAST12 = 0x20      # timers 1 & 2 clocked from the 1.79 MHz master clock
CTL_JOIN12 = 0x40      # timers 1 & 2 joined into one 16-bit timer
CTL_JOIN34 = 0x80      # timers 3 & 4 joined into one 16-bit timer

# AUDC distortion values whose pure-tone / volume-only bit (0x10) selects a
# pure tone rather than volume-only output: 10 (A) and 14 (E).
PURE_DIST = (0xA0, 0xE0)


class DumpError(ValueError):
    pass


def _frame_freqs(clock, audf, ctl):
    """Output frequency (Hz) of each channel for one frame.

    The 64 kHz (master/28) and 15 kHz (master/114) timer clocks are derived
    from the machine clock; the high-clock option feeds the master clock
    straight into the timer. The 64 kHz path divides the timer underflow by
    two for the audio output, the 15 kHz and 1.79 MHz paths do not. Joined
    timer pairs form a 16-bit divider (low AUDF + high AUDF*256) clocked
    from the low channel's timer clock.
    """
    freq = [None] * 4
    join = [(ctl & CTL_JOIN12) != 0, (ctl & CTL_JOIN34) != 0]
    for pair in range(2):
        lo, hi = 2 * pair, 2 * pair + 1
        if join[pair]:
            divider = audf[lo] + audf[hi] * 256 + 1
            if pair == 0:
                # Joined 1+2: 15 kHz (bit 0) or 64 kHz clock; no high clock.
                if ctl & CTL_15KHZ:
                    f = clock / 114 / divider
                else:
                    f = clock / 28 / (2 * divider)
            else:
                # Joined 3+4: 1.79 MHz (bit 4) or 64 kHz clock.
                if ctl & CTL_FAST34:
                    f = clock / divider
                else:
                    f = clock / 28 / (2 * divider)
            freq[lo] = freq[hi] = f
        else:
            for ch in (lo, hi):
                divider = audf[ch] + 1
                if ch < 2 and ctl & CTL_15KHZ:
                    freq[ch] = clock / 114 / divider
                elif ch < 2 and ctl & CTL_FAST12:
                    freq[ch] = clock / divider
                elif ch >= 2 and ctl & CTL_FAST34:
                    freq[ch] = clock / divider
                else:
                    freq[ch] = clock / 28 / (2 * divider)
    return freq


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
        freq = _frame_freqs(clock, audf, ctl)
        for ch in range(4):
            vol = audc[ch] & 15
            if not vol:
                continue
            c = chans[ch]
            c["sounding_frames"] += 1
            if ch == 1 and ctl & CTL_HIPASS2 or ch == 2 and ctl & CTL_HIPASS3 \
                    or ch == 3 and ctl & CTL_HIPASS4:
                c["filtered_frames"] += 1
            if audc[ch] & 0x10:
                c["volume_only_frames"] += 1
            if (audc[ch] & 0xF0) in PURE_DIST:
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
