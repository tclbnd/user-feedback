"""Electronic Arts ADPCM audio (.eas): packets of 28-sample blocks, mono or stereo."""
import zlib

MAGIC = b"EAADPCM\0"
TABLE = (0, 240, 460, 392, 0, 0, -208, -220, 0, 1, 3, 4, 7, 8, 10, 11, 0, -1, -3, -4)


class EaError(ValueError):
    pass


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _s4(v):
    return v - 16 if v & 8 else v


def _clip(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def decode_packet(pkt, ch):
    if len(pkt) < 12:
        raise EaError("packet is too short")
    coded = int.from_bytes(pkt[0:4], "little", signed=True)
    nb = (len(pkt) - 12) // (30 if ch == 2 else 15) * 28
    coded = nb
    prev_l, cur_l, prev_r, cur_r = (_s16(int.from_bytes(pkt[k:k + 2], "little")) for k in range(4, 12, 2))
    p = 12
    out = []
    for _ in range(coded // 28):
        b = pkt[p]
        p += 1
        c1l, c2l = TABLE[(b >> 4) + 4], TABLE[b >> 4]
        c1r, c2r = TABLE[(b & 0x0F) + 4], TABLE[b & 0x0F]
        b = pkt[p]
        p += 1
        sl, sr = 20 - (b >> 4), 20 - (b & 0x0F)
        for _ in range(28 if ch == 2 else 14):
            b = pkt[p]
            p += 1
            n = ((b >> 4) * (1 << sl) + cur_l * c1l + prev_l * c2l) >> 8
            prev_l, cur_l = cur_l, n
            out.append(cur_l)
            if ch == 2:
                n = ((b & 0x0F) * (1 << sr) + cur_r * c1r + prev_r * c2r) >> 8
                prev_r, cur_r = cur_r, n
            else:
                n = ((b & 0x0F) * (1 << sl) + cur_l * c1l + prev_l * c2l) >> 8
                prev_l, cur_l = cur_l, n
            out.append(cur_r if ch == 2 else cur_l)
    return out


def parse(data):
    if len(data) < 16 or data[:8] != MAGIC:
        raise EaError("not an EA ADPCM stream")
    ch = data[8]
    rate = int.from_bytes(data[10:14], "little")
    count = int.from_bytes(data[14:16], "little")
    if ch not in (1, 2) or not rate:
        raise EaError("bad stream header")
    pos, packets, pcm = 16, [], []
    for _ in range(count):
        if pos + 4 > len(data):
            raise EaError("packet table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pkt = data[pos + 4:pos + 4 + n]
        if len(pkt) < n:
            raise EaError("packet is truncated")
        pos += 4 + n
        try:
            s = decode_packet(pkt, ch)
            packets.append({"ok": True, "samples": len(s) // ch})
            pcm += s
        except EaError:
            packets.append({"ok": False, "samples": 0})
    frames = len(pcm) // ch
    pcm = [_clip(v) for v in pcm]
    return {"channels": ch, "rate": rate, "packets": packets, "samples": frames,
            "duration": round(frames / rate, 4), "peak": max((abs(v) for v in pcm), default=0),
            "crc32": "%08x" % zlib.crc32(b"".join(v.to_bytes(2, "little", signed=True) for v in pcm))}
