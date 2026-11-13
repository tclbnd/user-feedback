#!/usr/bin/env python3
"""Parse a .zaf framed audio container into a frame-status report.

Container layout
----------------
8-byte magic ``ZQAF0001`` followed by concatenated frames.  Each frame
has a 12-byte big-endian header ``seq(u32) | length(u32) | crc32(u32)``
and ``length`` payload bytes.  The CRC is computed over
``seq | length | payload`` (the 8-byte header prefix plus the payload;
the CRC field itself is not covered) using :func:`zlib.crc32`.

Recovery policy (no whole-stream failure on a bad frame)
--------------------------------------------------------
* ``ok``          - the full frame is present and the CRC matches.
* ``bad-payload`` - the 12-byte header is parseable and ``length`` keeps
                    the claimed frame inside the stream, but the CRC
                    mismatches (payload bit flip, or a damaged seq/crc
                    field with a still-legal length).  The frame is
                    skipped at frame granularity; its exact byte range is
                    in the frame table, its payload is never recovered
                    and never enters a region.
* ``bad-header``  - ``length`` overruns the remaining bytes, i.e. the
                    header fields are illegal.  A gap is recorded at the
                    header offset and the parser resyncs byte by byte.
* ``truncated``   - the stream ends inside a frame and no resync point
                    exists afterwards.  A gap is recorded and parsing
                    stops; that terminal gap marks the unrecoverable
                    tail.

Resync is a pure byte scan for the next offset on which a complete
CRC-valid frame starts, so parsing the same bytes always produces the
same result and recovery from an arbitrary start offset reproduces the
corresponding suffix of a full-stream parse (see :func:`parse_stream`).
"""
import argparse
import json
import struct
import sys
import zlib

MAGIC = b"ZQAF0001"
HEADER_LEN = 12
U32_MASK = 0xFFFFFFFF


def _valid_frame_at(data, off):
    """Return ``(seq, length, crc, end)`` if a complete CRC-valid frame
    starts at *off*, otherwise ``None``."""
    n = len(data)
    if off < 0 or off + HEADER_LEN > n:
        return None
    seq, length, crc = struct.unpack(">III", data[off:off + HEADER_LEN])
    end = off + HEADER_LEN + length
    if end > n:
        return None
    actual = zlib.crc32(data[off:off + 8] + data[off + HEADER_LEN:end]) & U32_MASK
    if actual != crc:
        return None
    return seq, length, crc, end


def find_resync(data, start, *, before=None):
    """First offset >= *start* (and < *before*, if given) on which a
    complete CRC-valid frame starts, or ``None``.

    The scan is deterministic: it always returns the earliest candidate,
    which is what makes repeated parses and arbitrary-start recovery
    produce identical results.
    """
    n = len(data)
    if before is None or before > n:
        before = n
    pos = start
    while pos + HEADER_LEN <= n and pos < before:
        if _valid_frame_at(data, pos) is not None:
            return pos
        pos += 1
    return None


def parse_stream(data, base=0):
    """Parse a buffer of concatenated frames (magic already stripped).

    *base* is added to every offset in the report.  Passing a suffix
    ``data[a:]`` with ``base=a`` therefore yields exactly the frames,
    gaps and regions a full-stream parse reports at offsets >= a, which
    is the arbitrary-start recovery consistency guarantee.

    Returns ``{"frames", "gaps", "regions", "seq_gaps", "summary"}``.
    Frame byte ranges are ``[offset, offset + 12 + length)``; gap
    spans run from a gap's ``offset`` to the next frame/gap offset or
    to end-of-stream for a terminal truncated gap.
    """
    n = len(data)
    frames = []
    gaps = []
    seq_gaps = []
    regions = []
    region = None  # [start_offset, end_offset, ok_frame_count]
    pos = 0

    def close_region():
        nonlocal region
        if region is not None:
            regions.append({"start": region[0], "end": region[1],
                            "frames": region[2]})
            region = None

    def extend_region(abs_off, abs_end):
        nonlocal region
        if region is None:
            region = [abs_off, abs_end, 1]
        else:
            region[1] = abs_end
            region[2] += 1

    while pos < n:
        abs_off = base + pos

        # Fewer than 12 bytes left: the header itself is cut -> truncated.
        if n - pos < HEADER_LEN:
            gaps.append({"offset": abs_off, "reason": "truncated"})
            close_region()
            break  # no complete frame can follow: unrecoverable tail

        seq, length, crc = struct.unpack(">III", data[pos:pos + HEADER_LEN])
        end = pos + HEADER_LEN + length

        # Claimed frame runs past EOF.  Resync to distinguish a garbage
        # length field (bad-header) from a stream cut mid-frame (truncated).
        if end > n:
            resync = find_resync(data, pos + 1)
            if resync is not None:
                gaps.append({"offset": abs_off, "reason": "bad-header"})
                close_region()
                pos = resync
                continue
            gaps.append({"offset": abs_off, "reason": "truncated"})
            close_region()
            break  # nothing CRC-valid follows: unrecoverable tail

        # Whole frame is present and the length field stays inside the
        # stream: it is trusted for extent.  A CRC failure then means the
        # bytes (payload or header fields) are damaged -> bad-payload, so
        # the exact frame byte range stays in the frame table and the
        # frame is skipped at frame granularity.  An illegal length is the
        # overrun branch above, which is the only bad-header path.
        if _valid_frame_at(data, pos) is not None:
            status = "ok"
        else:
            status = "bad-payload"

        frames.append({"seq": seq, "offset": abs_off,
                       "length": length, "status": status})

        # Record every seq discontinuity truthfully (u32 wrap aware).
        if len(frames) >= 2:
            prev_seq = frames[-2]["seq"]
            expected = (prev_seq + 1) & U32_MASK
            if seq != expected:
                seq_gaps.append({"offset": abs_off, "prev_seq": prev_seq,
                                 "expected": expected, "actual": seq})

        if status == "ok":
            extend_region(abs_off, base + end)
        else:
            # Bad-payload payload is not decodable: split regions here.
            close_region()
        pos = end

    close_region()

    ok_frames = [f for f in frames if f["status"] == "ok"]
    summary = {
        "frames_total": len(frames),
        "frames_ok": len(ok_frames),
        "frames_bad": len(frames) - len(ok_frames),
        "gaps": len(gaps),
        "bytes_recovered": sum(f["length"] for f in ok_frames),
    }
    return {"frames": frames, "gaps": gaps, "regions": regions,
            "seq_gaps": seq_gaps, "summary": summary}


def extract_ok_payload(data, report):
    """Concatenate the payload bytes of every ok frame, in parse order."""
    chunks = []
    for frame in report["frames"]:
        if frame["status"] != "ok":
            continue
        off = frame["offset"]
        length = frame["length"]
        chunks.append(data[off + HEADER_LEN:off + HEADER_LEN + length])
    return b"".join(chunks)


def parse(path):
    """Read *path*, validate the magic and return ``(raw_bytes, report)``.

    Exits with code 2 (stderr containing the word ``magic``) when the
    file cannot be read or does not start with the container magic.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        print(f"magic mismatch: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not data.startswith(MAGIC):
        print(f"magic mismatch: {path} does not start with ZQAF0001",
              file=sys.stderr)
        sys.exit(2)
    return data, parse_stream(data[len(MAGIC):], base=len(MAGIC))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Recover frames from a .zaf framed audio container.")
    parser.add_argument("input", help="input .zaf stream")
    parser.add_argument("output", help="output JSON report")
    parser.add_argument("--extract", metavar="RAW",
                        help="also write recovered ok-frame payloads "
                             "(concatenated, in parse order)")
    args = parser.parse_args(argv)

    data, report = parse(args.input)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    if args.extract:
        with open(args.extract, "wb") as handle:
            handle.write(extract_ok_payload(data, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
