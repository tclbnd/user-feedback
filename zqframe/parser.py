import zlib
from .format import MAGIC, HEADER_SIZE, unpack_header, header_valid, payload_crc

# Frame status values
OK = "ok"
BAD_HEADER = "bad_header"
BAD_PAYLOAD = "bad_payload"
TRUNCATED = "truncated"


def _find_magic(buf, start):
    # Return index of next MAGIC at or after start, or -1.
    return buf.find(MAGIC, start)


def parse_stream(data, base_offset=0):
    # Parse a (possibly damaged) ZQFR stream.
    #
    # data:        bytes of the stream (or a recovered suffix of it)
    # base_offset: absolute byte offset of data[0] in the original stream,
    #              so results from arbitrary restart points are directly
    #              comparable with a full parse.
    #
    # Returns a dict:
    #   frames:   list of per-frame status records, in stream order:
    #               offset       absolute byte offset of frame start
    #               header_bytes bytes of header present (0..HEADER_SIZE)
    #               payload_len  declared payload length (None if header bad)
    #               seq          sequence number (None if header bad)
    #               status       ok / bad_header / bad_payload / truncated
    #               end          absolute offset one past the frame
    #                            (for bad_header: offset of resync point)
    #   gaps:     list of (abs_start, abs_end) missing byte intervals
    #             (bad frames, truncated tails, unparseable regions)
    #   seq_gaps: list of (prev_seq, next_seq) discontinuities observed
    #             between consecutive successfully parsed frames
    #   intervals: list of (abs_start, abs_end) decodable byte intervals,
    #              i.e. maximal runs of consecutive ok frames whose seq
    #              numbers are also consecutive.
    frames = []
    gaps = []
    pos = 0
    n = len(data)
    while pos < n:
        if data[pos:pos + 4] != MAGIC:
            nxt = _find_magic(data, pos + 1)
            if nxt < 0:
                gaps.append((base_offset + pos, base_offset + n))
                pos = n
                break
            gaps.append((base_offset + pos, base_offset + nxt))
            pos = nxt
            continue
        # Candidate frame at pos.
        hdr = data[pos:pos + HEADER_SIZE]
        if len(hdr) < HEADER_SIZE:
            frames.append({
                "offset": base_offset + pos,
                "header_bytes": len(hdr),
                "payload_len": None,
                "seq": None,
                "status": TRUNCATED,
                "end": base_offset + n,
            })
            gaps.append((base_offset + pos, base_offset + n))
            pos = n
            break
        if not header_valid(hdr):
            # Header corrupt: declared length/seq untrustworthy.
            # Resync at the next magic after this one.
            nxt = _find_magic(data, pos + 4)
            end = nxt if nxt >= 0 else n
            frames.append({
                "offset": base_offset + pos,
                "header_bytes": HEADER_SIZE,
                "payload_len": None,
                "seq": None,
                "status": BAD_HEADER,
                "end": base_offset + end,
            })
            gaps.append((base_offset + pos, base_offset + end))
            pos = end
            continue
        _, seq, plen, pcrc, _ = unpack_header(hdr)
        pstart = pos + HEADER_SIZE
        pend = pstart + plen
        if pend > n:
            frames.append({
                "offset": base_offset + pos,
                "header_bytes": HEADER_SIZE,
                "payload_len": plen,
                "seq": seq,
                "status": TRUNCATED,
                "end": base_offset + n,
            })
            gaps.append((base_offset + pos, base_offset + n))
            pos = n
            break
        payload = data[pstart:pend]
        if payload_crc(payload) != pcrc:
            frames.append({
                "offset": base_offset + pos,
                "header_bytes": HEADER_SIZE,
                "payload_len": plen,
                "seq": seq,
                "status": BAD_PAYLOAD,
                "end": base_offset + pend,
            })
            gaps.append((base_offset + pos, base_offset + pend))
        else:
            frames.append({
                "offset": base_offset + pos,
                "header_bytes": HEADER_SIZE,
                "payload_len": plen,
                "seq": seq,
                "status": OK,
                "end": base_offset + pend,
            })
        pos = pend
    seq_gaps = []
    ok_frames = [f for f in frames if f["status"] == OK]
    for a, b in zip(ok_frames, ok_frames[1:]):
        if b["seq"] != a["seq"] + 1:
            seq_gaps.append((a["seq"], b["seq"]))
    intervals = intervals_from_frames(frames)
    return {
        "frames": frames,
        "gaps": gaps,
        "seq_gaps": seq_gaps,
        "intervals": intervals,
    }


def decode_intervals(data, result, base_offset=0):
    # Extract payload bytes for each decodable interval.
    # Returns list of (interval, payload_bytes) with frames concatenated
    # in seq order. Only ok frames contribute; nothing is fabricated.
    out = []
    ok = [f for f in result["frames"] if f["status"] == OK]
    for (s, e) in result["intervals"]:
        chunks = []
        for f in ok:
            if s <= f["offset"] and f["end"] <= e:
                pstart = f["offset"] - base_offset + HEADER_SIZE
                chunks.append(bytes(data[pstart:pstart + f["payload_len"]]))
        out.append(((s, e), b"".join(chunks)))
    return out


def intervals_from_frames(frames):
    # Compute decodable intervals from an unordered collection of frame
    # records (as produced by parse_stream). Result depends only on the
    # set of ok frames, not on read order: frames are sorted by offset
    # first, so randomly reordered reads yield identical intervals.
    ok = sorted((f for f in frames if f["status"] == OK),
                key=lambda f: f["offset"])
    intervals = []
    run = None
    prev = None
    for f in ok:
        if run is not None and f["seq"] == prev["seq"] + 1 \
                and f["offset"] == prev["end"]:
            run = (run[0], f["end"])
        else:
            if run is not None:
                intervals.append(run)
            run = (f["offset"], f["end"])
        prev = f
    if run is not None:
        intervals.append(run)
    return intervals
