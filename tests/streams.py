"""Builders for small synthetic DTS core streams used by the tests."""
RATES = {8000: 1, 16000: 2, 32000: 3, 11025: 6, 22050: 7, 44100: 8, 12000: 11, 24000: 12, 48000: 13}


def frame(layout="BE16", rate=48000, amode=9, lfe=True, nblks=15, fsize=2015,
          kbps_index=24, crc=False, ext_id=None, ftype=1, spare=3):
    """One core frame.  fsize is FSIZE (frame size - 1, in the 16-bit domain).

    *layout* is BE16/LE16 for plain 16-bit words or BE14/LE14 for 14 bits per word;
    *spare* sets the two unused bits of each 14-bit word.
    """
    fields = [(1, ftype), (5, 31), (1, 1 if crc else 0), (7, nblks), (14, fsize), (6, amode), (4, RATES[rate]),
              (5, kbps_index), (1, 0), (1, 0), (1, 0), (1, 0), (1, 0), (3, ext_id or 0),
              (1, 1 if ext_id is not None else 0), (1, 0), (2, 2 if lfe else 0), (1, 0)]
    bits = "01111111111111101000000000000001" + ("111111" if layout.endswith("14") else "")
    for n, v in fields:
        bits += format(v, "0%db" % n)
    total = (fsize + 1) * 8
    fill = "0101010101010101"
    if total > len(bits):
        bits += (fill * (total // 16 + 2))[:total - len(bits)]
    else:
        bits = bits[:total]
    if layout.endswith("14"):
        words = []
        for i in range(0, len(bits), 14):
            chunk = bits[i:i + 14].ljust(14, "0")
            words.append(int(chunk, 2) | (spare << 14))
        raw = b"".join(w.to_bytes(2, "big") for w in words)
    else:
        raw = int(bits, 2).to_bytes(len(bits) // 8, "big")
    if layout.startswith("LE"):
        raw = b"".join(raw[i + 1:i + 2] + raw[i:i + 1] for i in range(0, len(raw), 2))
    return raw


def stream(n, **kw):
    return b"".join(frame(**kw) for _ in range(n))


