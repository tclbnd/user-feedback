"""Builders for small synthetic DTS core streams used by the tests."""
RATES = {8000: 1, 16000: 2, 32000: 3, 11025: 6, 22050: 7, 44100: 8, 12000: 11, 24000: 12, 48000: 13}


def frame(rate=48000, amode=9, lfe=True, nblks=15, fsize=2015, kbps_index=24):
    layout, crc, ext_id, ftype = "BE16", False, None, 1
    """One core frame. fsize is FSIZE (frame bytes - 1, in the 16-bit domain)."""
    fields = [(1, ftype), (5, 31), (1, 1 if crc else 0), (7, nblks), (14, fsize), (6, amode), (4, RATES[rate]),
              (5, kbps_index), (1, 0), (1, 0), (1, 0), (1, 0), (1, 0), (3, ext_id or 0), (1, 1 if ext_id is not None else 0),
              (1, 0), (2, 2 if lfe else 0), (1, 0)]
    bits = "01111111111111101000000000000001" + ("111111" if layout.endswith("14") else "")
    for n, v in fields:
        bits += format(v, "0%db" % n)
    total = (fsize + 1) * 8
    fill = "0101010101010101"
    bits += (fill * (total // 16 + 2))[:total - len(bits)] if total > len(bits) else ""
    if layout.endswith("14"):
        words = []
        for i in range(0, len(bits) - 13, 14):
            w = int(bits[i:i + 14], 2)
            if w & 0x2000:
                w |= 0xC000
            words.append(w)
        raw = b"".join(w.to_bytes(2, "big") for w in words)
    else:
        raw = int(bits, 2).to_bytes(len(bits) // 8, "big")
    if layout.startswith("LE"):
        raw = b"".join(raw[i + 1:i + 2] + raw[i:i + 1] for i in range(0, len(raw), 2))
    return raw


def stream(n, **kw):
    return b"".join(frame(**kw) for _ in range(n))


