"""Generate deterministic synthetic MKV fixtures and truncation variants.

The fixtures deliberately exercise every repair feature:
  * video track with keyframe SimpleBlocks + inter-coded BlockGroups
  * audio track carrying Xiph / EBML / fixed-size lacing
  * clusters using both fixed and unknown EBML sizes
  * a fully valid "reference" file each truncated sample is compared against
No third-party modules are used.
"""

import os
import struct

# Element ids used when synthesising fixtures.
EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
SEEKHEAD = 0x114D9B74
SEEK = 0x4DBB
SEEKID = 0x53AB
SEEKPOS = 0x53AC
INFO = 0x1549A966
TIMESCALE = 0x2AD7B1
DURATION = 0x4489
MUXAPP = 0x4D80
TRACKS = 0x1654AE6B
TRACKENTRY = 0xAE
TRACKNUM = 0xD7
TRACKUID = 0x73C5
TRACKTYPE = 0x83
CODECID = 0x86
CODECPRIV = 0x63A2
FLAGLACING = 0x9C
VIDEO = 0xE0
AUDIO = 0xE1
PIXELW = 0xB0
PIXELH = 0xBA
SAMPLING = 0xB5
CHANNELS = 0x9F
CLUSTER = 0x1F43B675
TIMECODE = 0xE7
SIMPLEBLOCK = 0xA3
BLOCKGROUP = 0xA0
BLOCK = 0xA1
REFBLOCK = 0xFB
PREVSIZE = 0xAB
CUES = 0x1C53BB6B
CUEPOINT = 0xBB
CUETIME = 0xB3
CUETRACKPOS = 0xB7
CUETRACK = 0xF7
CUECLUSTERPOS = 0xF1
CUERELPOS = 0xF0


def vint_width(value):
    for width in range(1, 9):
        if value < (1 << (7 * width)) - 1:
            return width
    raise ValueError("too big")


def vint(value, width=None):
    width = width or vint_width(value)
    marker = 0x80 >> (width - 1)
    out = bytearray(width)
    rest = value
    for i in range(width - 1, -1, -1):
        out[i] = rest & 0xFF
        rest >>= 8
    out[0] |= marker
    return bytes(out)


def unknown_vint(width=8):
    return bytes([0x01 << (8 - width)]) + b"\xff" * (width - 1)


def eid(element_id):
    width = (element_id.bit_length() + 7) // 8
    return element_id.to_bytes(width, "big")


def uint_bytes(value):
    if value == 0:
        return b"\x00"
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def elem(element_id, payload):
    return eid(element_id) + vint(len(payload)) + bytes(payload)


def float_bytes(value):
    return struct.pack(">d", value)


def make_ebml_header():
    payload = (
        elem(0x4286, b"\x01") +                       # EBMLVersion
        elem(0x42F7, b"\x01") +                       # EBMLReadVersion
        elem(0x42F2, b"\x04") +                       # EBMLMaxIDLength
        elem(0x42F3, b"\x08") +                       # EBMLMaxSizeLength
        elem(0x4282, b"matroska") +                   # DocType
        elem(0x4287, b"\x04") +                       # DocTypeVersion
        elem(0x4285, b"\x02")                         # DocTypeReadVersion
    )
    return elem(EBML_HEADER, payload)


def make_info(duration_ticks):
    payload = (
        elem(TIMESCALE, uint_bytes(1_000_000)) +
        elem(DURATION, float_bytes(duration_ticks)) +
        elem(MUXAPP, b"synthetic-fixture-1.0")
    )
    return elem(INFO, payload)


def make_tracks():
    video_private = b"VPRIV" + bytes(range(16))
    video = (
        elem(TRACKNUM, uint_bytes(1)) +
        elem(TRACKUID, uint_bytes(1001)) +
        elem(TRACKTYPE, b"\x01") +
        elem(FLAGLACING, b"\x00") +
        elem(CODECID, b"V_MPEG4/ISO/AVC") +
        elem(CODECPRIV, video_private) +
        elem(VIDEO, elem(PIXELW, uint_bytes(64)) +
             elem(PIXELH, uint_bytes(48)))
    )
    audio_private = b"APRIV" + bytes(range(8))
    audio = (
        elem(TRACKNUM, uint_bytes(2)) +
        elem(TRACKUID, uint_bytes(2002)) +
        elem(TRACKTYPE, b"\x02") +
        elem(FLAGLACING, b"\x01") +
        elem(CODECID, b"A_OPUS") +
        elem(CODECPRIV, audio_private) +
        elem(AUDIO, elem(SAMPLING, float_bytes(48000.0)) +
             elem(CHANNELS, b"\x02"))
    )
    return elem(TRACKS, elem(TRACKENTRY, video) +
                elem(TRACKENTRY, audio))


def block_payload(track, rel_time, flags, frames, lacing):
    head = vint(track) + struct.pack(">h", rel_time) + bytes([flags])
    if lacing == 0:
        assert len(frames) == 1
        return head + frames[0]
    head += bytes([len(frames) - 1])
    sizes = [len(f) for f in frames]
    body = b"".join(frames)
    if lacing == 2:                               # fixed
        return head + body
    if lacing == 1:                               # Xiph
        prefix = b""
        for size in sizes[:-1]:
            while size >= 255:
                prefix += b"\xff"
                size -= 255
            prefix += bytes([size])
        return head + prefix + body
    if lacing == 3:                               # EBML
        prefix = b""
        for i, size in enumerate(sizes[:-1]):
            value = size if i == 0 else size - sizes[i - 1]
            prefix += _signed_vint(value)
        return head + prefix + body
    raise ValueError("bad lacing")


def _signed_vint(value):
    for width in range(1, 9):
        bias = 1 << (7 * width - 1)
        encoded = value + bias
        if 0 <= encoded < (1 << (7 * width)) - 1:
            return vint(encoded, width)
    raise ValueError("signed vint range")


def frame_bytes(kind, cluster_idx, block_idx, frame_idx, size):
    tag = ("%s-c%02d-b%02d-f%02d-" %
           (kind, cluster_idx, block_idx, frame_idx)).encode()
    filler = bytes(((i * 31 + cluster_idx * 7 + block_idx) & 0xFF)
                   for i in range(size - len(tag)))
    return tag + filler[:max(0, size - len(tag))]


def build_reference(path, unknown_clusters=False, unknown_segment=False):
    """Build the complete, undamaged reference MKV."""
    # Timeline: 24 clusters at 100ms; 25fps video (40ms frames).
    cluster_count = 24
    clusters = []
    cue_inputs = []
    for ci in range(cluster_count):
        tc = ci * 100
        children = elem(TIMECODE, uint_bytes(tc))
        # Video: keyframe every 5 clusters (500ms), else BlockGroup P-frame.
        keyframe = (ci % 5 == 0)
        vframe = frame_bytes("V", ci, 0, 0, 120 + (ci % 7) * 4)
        v_payload = block_payload(1, 0, 0x80 if keyframe else 0x00,
                                  [vframe], 0)
        if keyframe:
            children += elem(SIMPLEBLOCK, v_payload)
            cue_inputs.append((ci, tc, 0, True, 1))
        else:
            group = (elem(BLOCK, v_payload) +
                     elem(REFBLOCK, struct.pack(">h", -1)))
            children += elem(BLOCKGROUP, group)
        # Second video frame at +40ms inside even clusters (SimpleBlock P).
        if ci % 2 == 0:
            v2 = frame_bytes("V", ci, 1, 0, 96)
            p2 = block_payload(1, 40, 0x00, [v2], 0)
            children += elem(SIMPLEBLOCK, p2)
        # Audio: Xiph / EBML / fixed lacing rotate across clusters.
        lacing = (1, 3, 2)[ci % 3]
        base = 48 + (ci % 4) * 8
        if lacing == 1:
            sizes = [base, base + 255, base + 256, base]
        elif lacing == 3:
            sizes = [base, base + 30, base - 10, base + 5]
        else:
            sizes = [base] * 4
        frames = [frame_bytes("A", ci, 2, fi, sizes[fi])
                  for fi in range(4)]
        a_flags = (lacing << 1)
        a_payload = block_payload(2, 20, a_flags, frames, lacing)
        children += elem(SIMPLEBLOCK, a_payload)
        if unknown_clusters:
            clusters.append(eid(CLUSTER) + unknown_vint(8) + children)
        else:
            clusters.append(elem(CLUSTER, children))

    info = make_info(cluster_count * 100)
    tracks = make_tracks()

    # Cues with placeholder positions; patched once clusters are laid out.
    def build_cues(positions):
        outer = b""
        for ci, time_ms, _rel, _kf, track in cue_inputs:
            cluster_rel, block_rel = positions[ci]
            tps = (
                elem(CUETRACK, uint_bytes(track)) +
                elem(CUECLUSTERPOS, uint_bytes(cluster_rel)) +
                elem(CUERELPOS, uint_bytes(block_rel))
            )
            point = (elem(CUETIME, uint_bytes(time_ms)) +
                     elem(CUETRACKPOS, tps))
            outer += elem(CUEPOINT, point)
        return elem(CUES, outer)

    def seek_entry(target_id, position):
        body = elem(SEEKID, eid(target_id)) + \
            elem(SEEKPOS, uint_bytes(position))
        return elem(SEEK, body)

    def build_seek_head(info_pos, tracks_pos, cues_pos):
        body = (seek_entry(INFO, info_pos) +
                seek_entry(TRACKS, tracks_pos) +
                seek_entry(CUES, cues_pos))
        return elem(SEEKHEAD, body)

    def resolve_block_positions(seek_head_blob):
        pos = len(seek_head_blob) + len(info) + len(tracks)
        resolved = []
        for blob in clusters:
            resolved.append((pos, _first_block_rel(blob)))
            pos += len(blob)
        return resolved, pos

    # Fixed point: SeekHead size changes while its entries' positions shift.
    seek_head = elem(SEEKHEAD, b"")
    for _ in range(8):
        _resolved, cues_pos = resolve_block_positions(seek_head)
        info_pos = len(seek_head)
        tracks_pos = info_pos + len(info)
        new_head = build_seek_head(info_pos, tracks_pos, cues_pos)
        if len(new_head) == len(seek_head):
            seek_head = new_head
            break
        seek_head = new_head
    resolved, _cues_pos = resolve_block_positions(seek_head)
    cues = build_cues(resolved)

    segment_payload = seek_head + info + tracks + b"".join(clusters) + cues
    if unknown_segment:
        segment_head = eid(SEGMENT) + unknown_vint(8)
    else:
        segment_head = eid(SEGMENT) + vint(len(segment_payload))
    data = make_ebml_header() + segment_head + segment_payload
    with open(path, "wb") as handle:
        handle.write(data)
    return data


def _first_block_rel(cluster_blob):
    # Skip cluster id + size, then Timecode element; return offset of the
    # first block element relative to the cluster start.
    off = 0
    idw, _id = _raw_vint(cluster_blob, off)
    sw, _size = _raw_vint(cluster_blob, off + idw)
    off += idw + sw
    # Timecode
    tidw, _tid = _raw_vint(cluster_blob, off)
    tsw, tsize = _raw_vint(cluster_blob, off + tidw)
    off += tidw + tsw + tsize
    return off


def _raw_vint(buf, off):
    first = buf[off]
    width = next((i + 1 for i in range(8) if first & (0x80 >> i)), 0)
    value = first & ((1 << (8 - width)) - 1)
    for k in range(1, width):
        value = (value << 8) | buf[off + k]
    return width, value


def truncate(src, dst, length):
    data = open(src, "rb").read()
    assert length < len(data)
    with open(dst, "wb") as handle:
        handle.write(data[:length])
    return len(data), length


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample_dir = os.path.join(base, "samples")
    os.makedirs(sample_dir, exist_ok=True)
    ref_path = os.path.join(sample_dir, "ref_full.mkv")
    ref_unknown_path = os.path.join(sample_dir, "ref_unknown.mkv")
    build_reference(ref_path)
    build_reference(ref_unknown_path, unknown_clusters=True,
                    unknown_segment=True)
    full = open(ref_path, "rb").read()
    full_unknown = open(ref_unknown_path, "rb").read()

    # Deterministic cut points spanning every structural boundary.
    cut_plan = [
        ("s1_cut_inside_cluster_block.mkv", ref_path,
         _offset_of(full, "mid-first-cluster")),
        ("s2_cut_at_cluster_boundary.mkv", ref_path,
         _offset_of(full, "cluster-boundary-6")),
        ("s3_cut_inside_laced_audio.mkv", ref_path,
         _offset_of(full, "inside-laced-audio-3")),
        ("s4_cut_after_info_before_tracks.mkv", ref_path,
         _offset_of(full, "after-info")),
        ("s5_cut_inside_segment_header.mkv", ref_path,
         _segment_payload(full) - 2),
        ("s6_unknown_cut_inside_cluster.mkv", ref_unknown_path,
         _offset_of(full_unknown, "mid-first-cluster")),
        ("s7_unknown_cut_cluster_boundary.mkv", ref_unknown_path,
         _offset_of(full_unknown, "cluster-boundary-6")),
        ("s8_cut_inside_final_cues.mkv", ref_path,
         _offset_of(full, "inside-cues")),
    ]
    manifest = []
    for name, src, length in cut_plan:
        original, cut = truncate(src, os.path.join(sample_dir, name), length)
        manifest.append({"file": name, "source": os.path.basename(src),
                         "original_size": original, "cut_size": cut})
    import json
    with open(os.path.join(sample_dir, "manifest.json"), "w") as handle:
        json.dump(manifest, handle, indent=2)
    for item in manifest:
        print(item)


def _segment_payload(data):
    # 4-byte EBML id + 1-byte size + payload, then 4-byte Segment id...
    off = 0
    idw, _ = _raw_vint(data, off)
    sw, hsize = _raw_vint(data, off + idw)
    seg = off + idw + sw + hsize
    sidw, _ = _raw_vint(data, seg)
    ssw, _ = _raw_vint(data, seg + sidw)
    return seg + sidw + ssw


def _offset_of(data, kind):
    # Reuse the verifier-grade element walker locally.
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import verify
    top = list(verify.elements(data, 0, len(data)))
    seg = next(t for t in top if t[0] == verify.SEGMENT)
    _i, _h, spoff, spend, _u = seg
    cluster_offsets = []
    cues_offset = None
    info_offset = tracks_offset = None
    for eid, hoff, poff, pend, unk in verify.segment_elements(
            data, spoff, spend):
        if eid == verify.CLUSTER:
            cluster_offsets.append((hoff, poff, pend))
        elif eid == verify.CUES:
            cues_offset = hoff
        elif eid == verify.INFO:
            info_offset = pend
        elif eid == verify.TRACKS:
            tracks_offset = hoff
    if kind == "mid-first-cluster":
        _h, poff, pend = cluster_offsets[0]
        return poff + (pend - poff) // 2
    if kind == "cluster-boundary-6":
        return cluster_offsets[6][0]               # exactly at cluster start
    if kind == "inside-laced-audio-3":
        _h, poff, pend = cluster_offsets[3][0], *cluster_offsets[3][1:]
        # walk to the audio SimpleBlock (3rd block-ish) and cut within it
        for cid, ch, cp, cpe, cu in verify.elements(data, poff, pend):
            if cid == verify.SIMPLEBLOCK:
                blk = verify.parse_block(data, cp, cpe)
                if blk["lacing"] != 0:
                    return cp + (cpe - cp) // 2
        return (poff + pend) // 2
    if kind == "after-info":
        return info_offset + 3                     # a few bytes into Tracks hdr
    if kind == "inside-cues":
        return cues_offset + 10
    raise ValueError(kind)


if __name__ == "__main__":
    main()
