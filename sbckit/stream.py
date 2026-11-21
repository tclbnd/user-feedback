"""Walk SBC streams, mSBC/HFP packets and A2DP payload dumps."""
from fractions import Fraction

from .header import SYNC, MSBC_SYNC, MSBC_FRAME_LENGTH, FrameError, parse

# H2 synchronisation header (HFP 1.6 Appendix A): byte 0 is fixed 0x01,
# byte 1 carries the 2-bit sequence number in its top two bits followed by
# the inverted sequence; the four possible values are 0x08, 0x38, 0xC8, 0xF8.
H2_FIRST = 0x01
H2_SYNC = {0x08: 0, 0x38: 1, 0xC8: 2, 0xF8: 3}
H2_PACKET_LENGTH = 60          # 2-byte H2 header + 57-byte mSBC frame + 1 padding

# A2DP media payload header: F/S/L flags plus the number of SBC frames.
A2DP_HEADER_SIZE = 1
A2DP_FRAGMENT_FIRST = 0x80
A2DP_FRAGMENT_LAST = 0x40
A2DP_FRAGMENT_CONT = 0x20


def _resync(data, pos):
    """Next position that could start a frame (sync word for either codec)."""
    nxt = len(data)
    for sync in (SYNC, MSBC_SYNC):
        hit = data.find(bytes([sync]), pos)
        if hit != -1:
            nxt = min(nxt, hit)
    return nxt


def _walk_raw(data):
    """Walk a raw SBC / bare mSBC stream.

    Returns (valid_frames, invalid_frames); bytes between frames are skipped.
    """
    out, pos, invalid = [], 0, 0
    while pos < len(data):
        if data[pos] not in (SYNC, MSBC_SYNC):
            pos = _resync(data, pos + 1)
            continue
        try:
            f = parse(data, pos)
        except FrameError:
            # Sync word with a header that cannot describe a valid frame:
            # count it and resync byte-by-byte so a stray sync byte cannot
            # make us skip the next real frame.
            invalid += 1
            pos = _resync(data, pos + 1)
            continue
        if pos + f["frame_length"] > len(data):
            break  # truncated trailing frame: nothing valid to count
        out.append(f)
        pos += f["frame_length"]
    return out, invalid


def frames_in(data):
    """Frames of a raw SBC / bare mSBC stream; bytes between frames are skipped."""
    return _walk_raw(data)[0]


def _is_h2(data):
    """True when the stream contains an H2-wrapped mSBC packet.

    The first packet header may itself be damaged, so the search covers the
    area of one 60-byte packet; requiring the 0xAD mSBC sync word after the
    H2 header keeps audio payload bytes from being mistaken for a header.
    """
    return _find_h2(data, 0) != -1


def _find_h2_header(data, start):
    """Next offset carrying an H2 header byte pair with a full packet left."""
    pos = start
    while True:
        hit = data.find(bytes([H2_FIRST]), pos)
        if hit == -1 or hit + H2_PACKET_LENGTH > len(data):
            return -1
        if data[hit + 1] in H2_SYNC:
            return hit
        pos = hit + 1


def _find_h2(data, start):
    """Next offset carrying an H2 header followed by a real mSBC frame.

    The strict check prevents audio payload bytes from masquerading as a
    header while resynchronising after a lost or corrupted packet.
    """
    pos = start
    while True:
        hit = data.find(bytes([H2_FIRST]), pos)
        if hit == -1 or hit + 2 + MSBC_FRAME_LENGTH > len(data):
            return -1
        if data[hit + 1] in H2_SYNC and data[hit + 2] == MSBC_SYNC:
            return hit
        pos = hit + 1


def _walk_h2(data):
    """Walk H2-wrapped mSBC packets (60 bytes each).

    Returns (valid_frames, invalid_frames, lost_frames).  Lost frames are the
    gaps detected between consecutive H2 sequence numbers (modulo 4).
    """
    out, invalid, lost = [], 0, 0
    pos, prev_seq = _find_h2_header(data, 0), None
    while pos != -1:
        seq = H2_SYNC[data[pos + 1]]
        if prev_seq is not None:
            gap = (seq - prev_seq) % 4
            if gap:
                lost += gap - 1
        prev_seq = seq
        frame_pos = pos + 2
        try:
            out.append(parse(data, frame_pos))
        except FrameError:
            invalid += 1  # valid H2 header, mSBC frame body unusable
        nxt = pos + H2_PACKET_LENGTH
        if (nxt + 1 < len(data) and data[nxt] == H2_FIRST and data[nxt + 1] in H2_SYNC):
            pos = nxt  # next packet sits where the fixed stride expects it
        else:
            pos = _find_h2(data, pos + 1)  # header lost/corrupted: resynchronise
    return out, invalid, lost


def a2dp_payloads(data):
    """Split a dump of A2DP media payloads (2-byte big-endian length prefix each)."""
    pos, out = 0, []
    while pos + 2 <= len(data):
        n = int.from_bytes(data[pos:pos + 2], "big")
        start = pos + 2
        if start + n > len(data):
            out.append(data[start:])  # truncated trailing dump entry
            break
        out.append(data[start:start + n])
        pos = start + n
    return out


def _frames_in_payload(payload):
    """Parse the SBC frames inside one A2DP media payload.

    Returns (frames, invalid).  Continuation fragments carry no new frame,
    and a frame spanning a fragment boundary is picked up in the last
    fragment, so only frames fully contained in a payload are counted.
    """
    if len(payload) < A2DP_HEADER_SIZE:
        return [], 0
    flags = payload[0]
    if flags & A2DP_FRAGMENT_CONT and not (flags & A2DP_FRAGMENT_LAST):
        return [], 0  # middle fragment: frame data only, no new frame start
    pos, out, invalid = A2DP_HEADER_SIZE, [], 0
    while pos < len(payload):
        if payload[pos] != SYNC:
            pos = _resync(payload, pos + 1)
            continue
        try:
            f = parse(payload, pos)
        except FrameError:
            invalid += 1
            pos = _resync(payload, pos + 1)
            continue
        if pos + f["frame_length"] > len(payload):
            break  # fragment boundary: rest of this frame is in another payload
        out.append(f)
        pos += f["frame_length"]
    return out, invalid


def a2dp_frames(data):
    out = []
    for payload in a2dp_payloads(data):
        frames, _ = _frames_in_payload(payload)
        out.extend(frames)
    return out


def _walk_a2dp(data):
    frames, invalid = [], 0
    for payload in a2dp_payloads(data):
        p_frames, p_invalid = _frames_in_payload(payload)
        frames.extend(p_frames)
        invalid += p_invalid
    return frames, invalid


def summary(data, a2dp=False):
    if a2dp:
        frames, invalid = _walk_a2dp(data)
        lost = 0
    elif _is_h2(data):
        frames, invalid, lost = _walk_h2(data)
    else:
        frames, invalid = _walk_raw(data)
        lost = 0
    if not frames:
        raise FrameError("no SBC frames")
    first = frames[0]
    samples = sum(f["samples"] for f in frames)
    return {
        "codec": first["codec"],
        "sample_rate": first["sample_rate"],
        "channel_mode": first["channel_mode"],
        "blocks": first["blocks"],
        "subbands": first["subbands"],
        "allocation": first["allocation"],
        "bitpool": first["bitpool"],
        "frame_length": first["frame_length"],
        "bitrate": first["bitrate"],
        "frames": len(frames),
        "duration": str(Fraction(samples, first["sample_rate"])),
        "invalid_frames": invalid,
        "lost_frames": lost,
    }
