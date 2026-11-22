"""Builders for small synthetic LTC recordings used by the tests."""
import math
import struct


def bcd_bits(v, n):
    return [(v >> i) & 1 for i in range(n)]


def frame_bits(tc, drop=False, user=0):
    hh, mm, ss, ff = tc
    g = [(user >> (4 * k)) & 0xF for k in range(8)]
    b = []
    b += bcd_bits(ff % 10, 4) + bcd_bits(g[0], 4) + bcd_bits(ff // 10, 2) + [1 if drop else 0, 0] + bcd_bits(g[1], 4)
    b += bcd_bits(ss % 10, 4) + bcd_bits(g[2], 4) + bcd_bits(ss // 10, 3) + [0] + bcd_bits(g[3], 4)
    b += bcd_bits(mm % 10, 4) + bcd_bits(g[4], 4) + bcd_bits(mm // 10, 3) + [0] + bcd_bits(g[5], 4)
    b += bcd_bits(hh % 10, 4) + bcd_bits(g[6], 4) + bcd_bits(hh // 10, 2) + [0, 0] + bcd_bits(g[7], 4)
    return b + [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]


def tc_add(tc, n, fps):
    hh, mm, ss, ff = tc
    t = ((hh * 60 + mm) * 60 + ss) * fps + ff + n
    return (t // (fps * 3600) % 24, t // (fps * 60) % 60, t // fps % 60, t % fps)


def biphase(bits, rate, fps, amp=12000, phase=0.0, invert=False):
    per_bit = rate / (80 * fps)
    out, level, t = [], -1 if invert else 1, phase
    for bit in bits:
        level = -level                                   # every cell starts with a transition
        half = t + per_bit / 2
        end = t + per_bit
        while len(out) < int(half):
            out.append(level * amp)
        if bit:
            level = -level
        while len(out) < int(end):
            out.append(level * amp)
        t = end
    return out


def wav(samples_per_channel, rate=48000, bits=16, extensible=False, odd_chunk=False):
    ch = len(samples_per_channel)
    n = len(samples_per_channel[0])
    width = bits // 8
    raw = bytearray()
    for i in range(n):
        for c in range(ch):
            v = int(samples_per_channel[c][i])
            if width == 1:
                raw.append((v >> 8) + 128)
            elif width == 2:
                raw += struct.pack("<h", v)
            elif width == 3:
                raw += (v * 256).to_bytes(3, "little", signed=True)
            else:
                raw += (v * 65536).to_bytes(4, "little", signed=True)
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, ch, rate, rate * ch * width, ch * width, bits, 22, bits, 0)
        fmt += struct.pack("<H", 1) + bytes.fromhex("000000001000800000aa00389b71")
    else:
        fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * width, ch * width, bits)
    extra = (b"LIST" + struct.pack("<I", 5) + b"INFOx" + b"\0") if odd_chunk else b""
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", len(raw)) + bytes(raw)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def ltc_wav(start=(1, 0, 0, 0), n=10, fps=25, rate=48000, user=0, channels=1, lead=0.37,
            drop=False, reverse=False, bits=16, invert=False, extensible=False,
            odd_chunk=False, dc=0):
    """An LTC recording; lead is where in the previous frame the recording starts."""
    stream = []
    for i in range(n):
        stream += frame_bits(tc_add(start, i, fps), drop, user)
    if reverse:
        stream = stream[::-1]
    lead_bits = int(lead * 80)
    before = frame_bits(tc_add(start, -1, fps), drop, user)
    if reverse:
        before = before[::-1]
    # the recording starts inside the previous frame (its tail, sync word included) and runs out after the last one
    stream = before[80 - lead_bits:] + stream + [0] * 24
    sig = [s + dc for s in biphase(stream, rate, fps, invert=invert)]
    chans = [[int(3000 * math.sin(i / 7.0)) for i in range(len(sig))] for _ in range(channels - 1)] + [sig]
    return wav(chans, rate, bits, extensible=extensible, odd_chunk=odd_chunk)
