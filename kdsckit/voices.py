"""Konami K053260 snapshots (.kdsc): four voices, PCM and KADPCM samples, reverse play, pan and pitch."""

CLOCK = 3579545
HDR = 8 + 48
# KADPCM nibble deltas as the K053260 applies them.  Nibble n selects an
# additive step: codes 0..7 step up 1,2,4,8,16,32,64,128 and codes 8..15 step
# down 128,64,32,16,8,4,2,1.  The accumulator is signed 8-bit and saturates
# at -128/127.
KADPCM = (1, 2, 4, 8, 16, 32, 64, 128, -128, -64, -32, -16, -8, -4, -2, -1)


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
    # Register $29 holds the ROM bank: its 16 bits are prefixed to the
    # 16-bit per-voice address, so a voice addresses bank<<16 + offset.
    bank = r[0x29]
    voices = []
    for i in range(4):
        # Voice blocks live at $08, $10, $18, $20 ($00..$07 is the master block).
        b = 8 + 8 * i
        pitch = r[b] | (r[b + 1] & 0x0F) << 8
        length = r[b + 2] | r[b + 3] << 8
        start = r[b + 4] | r[b + 5] << 8
        vol = r[b + 7] & 0x7F
        pan = (r[0x2C + i // 2] >> (3 * (i & 1))) & 7
        on, rev = bool(r[0x28] >> i & 1), bool(r[0x28] >> (i + 4) & 1)
        loop, adp = bool(r[0x2A] >> i & 1), bool(r[0x2A] >> (i + 4) & 1)
        # The pitch counter divides the chip clock by 128, so a full-scale
        # pitch of 4096 plays at CLOCK/128 samples per second.
        v = {"voice": i + 1, "on": on, "start": start, "length": length, "rate_hz": "%.2f" % (CLOCK / 128 * pitch / 4096),
             # Pan is a 3-bit weight: each side scales by weight/8 of volume.
             "volume": vol, "left": vol * (7 - pan) * 127 // 8, "right": vol * pan * 127 // 8,
             "loop": loop, "reverse": rev, "format": "kadpcm" if adp else "pcm",
             "samples": None, "peak": None, "sum": None, "last": None, "complete": None}
        if on:
            outs, ok = [], True
            # The programmed start points at a one-byte guard that is never
            # output.  Forward play post-increments: move to start+1 before the
            # first read and take length bytes start+1 .. start+length.
            # Reverse play mirrors the pass on the low side: the chip
            # pre-decrements length bytes to start-length (the reverse guard),
            # then reads length bytes from start-length-1 down to start-2*length.
            base = (bank << 16) + start
            if rev:
                offsets = range(-length - 1, -2 * length - 1, -1)
            else:
                offsets = range(1, length + 1)
            for off in offsets:
                a = base + off
                if not 0 <= a < len(rom):
                    ok = False
                    break
                byte = rom[a]
                if adp:
                    # Each byte holds two nibbles, high one played first.
                    for nib in (byte >> 4, byte & 15):
                        outs.append(max(-128, min(127, (outs[-1] if outs else 0) + KADPCM[nib])))
                else:
                    outs.append(_s8(byte))
            v.update(samples=len(outs), peak=max((abs(x) for x in outs), default=0), sum=sum(outs),
                     last=outs[-1] if outs else None, complete=ok)
        voices.append(v)
    return {"rom_size": len(rom), "voices_on": sum(v["on"] for v in voices), "voices": voices}
