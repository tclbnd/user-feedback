"""Independent, sample-exact reference implementation.

It shares NO code with overview.py: the source is parsed with struct.unpack
and every EDL segment is resolved frame by frame against the raw samples.
Range aggregation walks the EDL pieces directly instead of materialising the
whole timeline, but the arithmetic is identical to a full per-sample expand.
"""
import array
import json
import math
import sys
from pathlib import Path


def load_pcm(path, channels):
    raw = Path(path).read_bytes()
    if len(raw) % (channels * 2) != 0:
        raise ValueError("unaligned PCM")
    vals = array.array("h")
    vals.frombytes(raw)
    if sys.byteorder != "little":
        vals.byteswap()
    return vals, len(raw) // (channels * 2)


def iter_pieces(segments, start, end):
    """Yield ('S', src_index, length) / ('Z', -1, length) for [start,end)."""
    if end <= start:
        return
    boundaries = [0]
    for seg in segments:
        boundaries.append(boundaries[-1] + (
            seg.get("silence") if "silence" in seg
            else seg["end"] - seg["start"]))
    seg_idx = 0
    while boundaries[seg_idx + 1] <= start:
        seg_idx += 1
    pos = start
    while pos < end:
        seg = segments[seg_idx]
        seg_end = boundaries[seg_idx + 1]
        length = min(end, seg_end) - pos
        if "silence" in seg:
            yield ("Z", -1, length)
        else:
            yield ("S", seg["start"] + (pos - boundaries[seg_idx]), length)
        pos += length
        seg_idx += 1


def reference_query(source, channels, segments, ranges):
    pcm, _ = load_pcm(source, channels)
    out = []
    for q in ranges:
        start, end = q["start"], q["end"]
        rows = []
        for ch in range(channels):
            mn = None
            mx = None
            ss = 0
            count = 0
            for kind, src, length in iter_pieces(segments, start, end):
                for i in range(length):
                    if kind == "Z":
                        v = 0
                    else:
                        v = pcm[(src + i) * channels + ch]
                    if mn is None or v < mn:
                        mn = v
                    if mx is None or v > mx:
                        mx = v
                    ss += v * v
                    count += 1
            rows.append({
                "min": 0 if mn is None else mn,
                "max": 0 if mx is None else mx,
                "rms": 0.0 if count == 0 else math.sqrt(ss / count),
            })
        out.append({"id": q["id"], "channels": rows})
    return out


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    result = reference_query(
        spec["source"], spec["channels"], spec["segments"], spec["ranges"])
    print(json.dumps({"ranges": result}, sort_keys=True))


if __name__ == "__main__":
    main()
