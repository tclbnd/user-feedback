#!/usr/bin/env python3
"""Extract interleaved PCM from the RF64/WAVEFORMATEXTENSIBLE JSON subset.

The input/output contract is documented in README.md.  The important
invariant is that the number of frames in the file comes from the container
metadata (the RF64 ``ds64`` size when RIFF's 32-bit size is the
``0xFFFFFFFF`` sentinel), never from the length of a convenient in-memory
array.  The sample array is only the backing store and may be shorter
(truncated file) or longer (trailing bytes that are not part of the declared
data chunk) than the declared frame count.
"""

import json
import sys

RIFF_SIZE_SENTINEL = 0xFFFFFFFF


def _int(value, default=None):
    """Best-effort integer conversion without accepting booleans as indexes."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def available_frames(data, nch):
    """Frames fully present in the backing store (a partial tail is dropped)."""
    if nch <= 0:
        return 0
    return len(data) // nch


def frame_count(declared):
    """Return the true frame count for a parsed input document.

    ``riff_size`` is a 32-bit RIFF size.  When it is the RF64 sentinel the
    real size lives in ``ds64_size`` (expressed in frames by this task's
    subset).  A missing size field falls back to the available backing store,
    which keeps the reader useful for small hand-written fixtures.
    """
    nch = _int(declared.get("channels"), 0) or 0
    data = declared.get("interleaved") or []
    available = available_frames(data, nch)

    riff_size = _int(declared.get("riff_size"))
    if riff_size is None:
        return available
    if riff_size == RIFF_SIZE_SENTINEL:
        ds64_size = _int(declared.get("ds64_size"))
        if ds64_size is None:
            return available
        return max(0, ds64_size)
    return max(0, riff_size)


def _channel_index(channel, nch):
    if isinstance(channel, bool):
        return None
    index = _int(channel)
    if index is None or index < 0 or index >= nch:
        return None
    return index


def extract_request(request, data, nch, frames, available):
    """Extract one request, clipping to the declared end of file.

    Reads are frame aligned: a truncated backing store that ends part-way
    through a frame yields only the whole frames that are present, so every
    channel of a request reports the same number of samples.
    """
    request_id = request.get("id")
    start = _int(request.get("start_frame"))

    # Negative or past-the-end starts are errors.  In particular, do not let
    # Python's negative indexing wrap around to the end of the sample array.
    if start is None or start < 0 or start >= frames:
        return {"id": request_id, "error": "eof"}

    count = _int(request.get("count"), frames - start)
    if count is None or count < 0:
        count = 0

    # Clip to the declared data chunk, not to whatever the backing array
    # happens to contain.  Also never read past the last whole frame; missing
    # samples are dropped, never padded.
    end = min(start + count, frames, available)
    span = data[start * nch:end * nch]

    channel = request.get("channel", "all")
    if channel == "all":
        samples = list(span)
    else:
        index = _channel_index(channel, nch)
        samples = list(span[index::nch]) if index is not None else []

    return {
        "id": request_id,
        "channel": channel,
        "start_frame": start,
        "samples": samples,
    }


def process(document):
    """Apply all requests to a parsed input document."""
    channels = _int(document.get("channels"), 0) or 0
    data = document.get("interleaved") or []
    frames = frame_count(document)
    available = min(frames, available_frames(data, channels))

    results = [
        extract_request(request, data, channels, frames, available)
        for request in (document.get("requests") or [])
    ]
    return {"results": results, "frames_total": frames}


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        raise SystemExit("usage: rf64.py input.json output.json")

    with open(argv[1], "r", encoding="utf-8") as handle:
        document = json.load(handle)
    result = process(document)
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(result, handle)


if __name__ == "__main__":
    main()
