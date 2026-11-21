"""ProTracker module (.mod) header, patterns and samples."""


class ModError(ValueError):
    pass


# Format tags at offset 1080 with a fixed channel count.
_FIXED_TAGS = {
    "M.K.": 4, "M!K!": 4, "M&K!": 4, "N.T.": 4, "FLT4": 4,
    "FLT8": 8, "CD81": 8, "OKTA": 8, "OCTA": 8,
}


def _tag_channels(tag):
    """Channel count implied by the format tag, or None if it is not a tag."""
    if tag in _FIXED_TAGS:
        return _FIXED_TAGS[tag]
    if len(tag) == 4:
        # "6CHN" / "8CHN" style
        if tag[1:] == "CHN" and tag[0].isdigit() and tag[0] != "0":
            return int(tag[0])
        # "10CH" .. "32CH" style
        if tag[2:] == "CH" and tag[:2].isdigit():
            return int(tag[:2])
    return None


def _signed_nibble(v):
    """Finetune is a 4-bit two's complement value (-8..7)."""
    v &= 0x0F
    return v - 16 if v > 7 else v


def parse(data):
    tag = data[1080:1084].decode("latin-1") if len(data) >= 1084 else ""
    channels = _tag_channels(tag)
    if channels is not None:
        fmt, signature, nsamples = "31-sample", tag, 31
        length_off, orders_off, pat_start = 950, 952, 1084
    else:
        # Original Soundtracker layout: 15 samples, no format tag.
        fmt, signature, nsamples, channels = "15-sample", None, 15, 4
        length_off, orders_off, pat_start = 470, 472, 600
    if len(data) < pat_start:
        raise ModError("file too short")

    title = data[0:20].split(b"\0")[0].decode("latin-1").rstrip()
    samples = []
    for i in range(nsamples):
        h = data[20 + 30 * i:50 + 30 * i]
        # Lengths and loop points are stored in words; report them in bytes.
        length = int.from_bytes(h[22:24], "big") * 2
        loop_start = int.from_bytes(h[26:28], "big") * 2
        loop_length = int.from_bytes(h[28:30], "big") * 2
        samples.append({"index": i + 1,
                        "name": h[0:22].split(b"\0")[0].decode("latin-1").rstrip(),
                        "length": length, "finetune": _signed_nibble(h[24]), "volume": h[25],
                        "loop_start": loop_start, "loop_length": loop_length,
                        # A loop of a single word is the "no loop" convention.
                        "looped": loop_length > 2})
    song_length = data[length_off]
    orders = list(data[orders_off:orders_off + 128])
    used = orders[:song_length]
    patterns = max(used) + 1 if used else 0
    sample_start = pat_start + patterns * 64 * channels * 4
    return {"format": fmt, "signature": signature, "channels": channels, "title": title,
            "song_length": song_length, "orders": used, "patterns": patterns, "samples": samples,
            "pattern_data": data[pat_start:sample_start], "sample_data_offset": sample_start}


def cell(m, pattern, row, channel):
    """Return (sample, period, effect, param) of one pattern cell."""
    p = ((pattern * 64 + row) * m["channels"] + channel) * 4
    b = m["pattern_data"][p:p + 4]
    if len(b) < 4:
        return 0, 0, 0, 0
    # 15-sample modules keep the whole high nibble of byte 0 for the period.
    hi = 0 if m["format"] == "15-sample" else b[0] & 0xF0
    return hi | (b[2] >> 4), ((b[0] & 0x0F) << 8) | b[1], b[2] & 0x0F, b[3]
