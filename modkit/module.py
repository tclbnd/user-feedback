"""ProTracker module (.mod) header, patterns and samples."""


class ModError(ValueError):
    pass


def parse(data):
    if len(data) < 1084:
        raise ModError("file too short")
    tag = data[1080:1084]
    channels = 4
    title = data[0:20].split(b"\0")[0].decode("latin-1").rstrip()
    samples = []
    for i in range(31):
        h = data[20 + 30 * i:50 + 30 * i]
        samples.append({"index": i + 1, "name": h[0:22].split(b"\0")[0].decode("latin-1").rstrip(),
                        "length": int.from_bytes(h[22:24], "big"), "finetune": h[24] & 0x0F, "volume": h[25],
                        "loop_start": int.from_bytes(h[26:28], "big"), "loop_length": int.from_bytes(h[28:30], "big"),
                        "looped": int.from_bytes(h[28:30], "big") > 0})
    song_length = data[950]
    orders = list(data[952:1080])
    patterns = max(orders[:song_length]) + 1
    pat_start = 1084
    sample_start = pat_start + patterns * 64 * channels * 4
    return {"format": "31-sample", "signature": tag.decode("latin-1"), "channels": channels, "title": title,
            "song_length": song_length, "orders": orders[:song_length], "patterns": patterns, "samples": samples,
            "pattern_data": data[pat_start:sample_start], "sample_data_offset": sample_start}


def cell(m, pattern, row, channel):
    """Return (sample, period, effect, param) of one pattern cell."""
    p = ((pattern * 64 + row) * m["channels"] + channel) * 4
    b = m["pattern_data"][p:p + 4]
    if len(b) < 4:
        return 0, 0, 0, 0
    return (b[0] & 0xF0) | (b[2] >> 4), ((b[0] & 0x0F) << 8) | b[1], b[2] & 0x0F, b[3]
