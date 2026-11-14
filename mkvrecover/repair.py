"""Rebuild a truncated Matroska (.mkv / .webm) container.

The scanner understands fixed-size and unknown-size EBML elements, keeps
every byte of every *complete* block it can prove valid, rebuilds the
SeekHead and video Cues, and emits a manifest of recovered/discarded data.
"""

import hashlib
import json

from . import ebml
from .ebml import (
    ID_BLOCK, ID_BLOCK_GROUP, ID_CLUSTER, ID_CODEC_ID, ID_CODEC_PRIVATE,
    ID_CRC_32, ID_CUE_CLUSTER_POSITION, ID_CUE_POINT,
    ID_CUE_RELATIVE_POSITION, ID_CUE_TIME, ID_CUE_TRACK_POSITIONS,
    ID_CUE_TRACK, ID_CUES, ID_EBML_HEADER, ID_FLAG_LACING, ID_INFO,
    ID_REFERENCE_BLOCK, ID_SEEK, ID_SEEK_HEAD, ID_SEEK_ID, ID_SEEK_POSITION,
    ID_SEGMENT, ID_SIMPLE_BLOCK, ID_TIMESTAMP, ID_TIMESTAMP_SCALE,
    ID_TRACKS, ID_TRACK_ENTRY, ID_TRACK_NUMBER, ID_TRACK_TYPE, ID_VIDEO,
    ID_AUDIO, ID_VOID, Element, Truncated, child_text, child_uint,
    encode_element_id, encode_uint, encode_vint, find_child,
    find_children, read_element_id, read_uint, read_vint, scan_children,
)


class RepairError(Exception):
    """Locatable fatal error: message always carries a byte offset."""

    def __init__(self, message, offset):
        super().__init__("at byte %d: %s" % (offset, message))
        self.offset = offset


# --------------------------------------------------------------------------- #
# source scanning
# --------------------------------------------------------------------------- #

def scan_segment_level(buf, payload_start, payload_end):
    """Walk direct children of a Segment, including unknown-size Clusters.

    Returns a list of dicts: {kind, elem, resync_offset, note}.  An
    unknown-size Cluster ends at the first child boundary that is not a legal
    Cluster child (i.e. the next Segment-level sibling); see
    _unknown_cluster_end.
    """
    out = []
    offset = payload_start
    while offset < payload_end:
        start = offset
        try:
            id_len, eid = read_element_id(buf, offset)
            size_len, size = read_vint(buf, offset + id_len,
                                       allow_unknown=True)
        except Truncated:
            out.append({"kind": "truncated_header", "offset": start,
                        "length": payload_end - start})
            return out
        payload_off = offset + id_len + size_len
        unknown = size is None
        end = payload_off if unknown else payload_off + size
        if unknown:
            if eid != ID_CLUSTER:
                # Only Clusters legitimately use unknown size in practice.
                end = payload_end
                elem = Element(eid, start, id_len, size_len, None,
                               payload_off, end, unknown_size=True)
                out.append({"kind": "unknown", "elem": elem,
                            "resync_offset": end})
                offset = end
                continue
            end = _unknown_cluster_end(buf, payload_off, payload_end)
            elem = Element(ID_CLUSTER, start, id_len, size_len, None,
                           payload_off, end, unknown_size=True)
            out.append({"kind": "cluster_unknown", "elem": elem,
                        "resync_offset": end})
            offset = end
            continue
        if end > payload_end:
            elem = Element(eid, start, id_len, size_len, size,
                           payload_off, payload_end)
            elem.truncated = True
            elem.truncation = "payload"
            out.append({"kind": "truncated", "elem": elem})
            return out
        elem = Element(eid, start, id_len, size_len, size, payload_off, end)
        kind = "cluster" if eid == ID_CLUSTER else "other"
        out.append({"kind": kind, "elem": elem})
        offset = end
    return out


# Direct children that may legally appear inside a Cluster.  Anything else
# at a child boundary signals the start of the next Segment-level element.
CLUSTER_CHILD_IDS = frozenset((
    ID_TIMESTAMP, ID_SIMPLE_BLOCK, ID_BLOCK_GROUP, ID_CRC_32, ID_VOID,
    0xA7,   # Position
    0xAB,   # PrevSize
    0x5854,  # SilentTracks
    0xAF,   # EncryptedBlock
))


def _unknown_cluster_end(buf, payload_off, limit):
    """End offset of an unknown-size Cluster, found by walking children.

    Walking element by element (rather than searching for a Cluster id byte
    pattern) avoids false hits inside block payloads.  A child whose id is a
    legal Cluster child is skipped by its declared size; the first id outside
    that set marks the next Segment-level sibling.
    """
    off = payload_off
    while off < limit:
        try:
            id_len, eid = read_element_id(buf, off)
            size_len, size = read_vint(buf, off + id_len,
                                       allow_unknown=True)
        except Truncated:
            return limit
        if eid not in CLUSTER_CHILD_IDS:
            return off
        if size is None:
            return limit
        child_end = off + id_len + size_len + size
        if child_end > limit:
            return limit
        off = child_end
    return limit


def _validate_block_element(buf, elem, cluster_tc):
    """Return a block descriptor or None when the block must be discarded."""
    try:
        block = ebml.parse_block(buf, elem.payload_offset, elem.payload_end)
    except (ebml.EBMLSyntaxError, Truncated):
        return None
    if block["frames"] and all(length > 0 for _, length in block["frames"]):
        block["display_time"] = cluster_tc + block["relative_time"]
        block["raw_header_offset"] = elem.header_offset
        block["raw_payload_offset"] = elem.payload_offset
        block["raw_payload_length"] = elem.payload_end - elem.payload_offset
        return block
    return None


def _walk_cluster_children(buf, elem, cluster_tc):
    """Yield (child_elem, block_or_none, discarded) for one Cluster.

    Children are parsed manually so an unknown-size Cluster works too.
    """
    results = []
    offset = elem.payload_offset
    end = elem.payload_end
    while offset < end:
        start = offset
        try:
            id_len, eid = read_element_id(buf, offset)
            size_len, size = read_vint(buf, offset + id_len,
                                       allow_unknown=True)
        except Truncated:
            results.append((None, None, {
                "offset": start, "length": end - start,
                "reason": "truncated element header",
            }))
            return results
        payload_off = offset + id_len + size_len
        unknown = size is None
        child_end = end if unknown else payload_off + size
        if child_end > end:
            results.append((None, None, {
                "offset": start, "length": end - start,
                "reason": "element payload runs past cluster/file end",
            }))
            return results
        child = Element(eid, start, id_len, size_len, size, payload_off,
                        child_end, unknown_size=unknown)
        if eid == ID_BLOCK_GROUP:
            child.children, _ = scan_children(buf, payload_off, child_end,
                                              max_depth=1)
        block = None
        if eid == ID_SIMPLE_BLOCK:
            block = _validate_block_element(buf, child, cluster_tc)
            if block is None:
                results.append((None, None, {
                    "offset": start,
                    "length": child_end - start,
                    "reason": "unparseable or empty SimpleBlock",
                }))
                offset = child_end
                continue
            results.append((child, block, None))
        elif eid == ID_BLOCK_GROUP:
            block_elem = find_child(child, ID_BLOCK)
            discarded = None
            if block_elem is None:
                discarded = {"offset": start,
                             "length": child_end - start,
                             "reason": "BlockGroup without Block"}
            else:
                block = _validate_block_element(buf, block_elem, cluster_tc)
                if block is None:
                    discarded = {"offset": start,
                                 "length": child_end - start,
                                 "reason": "unparseable Block in BlockGroup"}
            if discarded:
                results.append((None, None, discarded))
            else:
                block["group_header_offset"] = child.header_offset
                block["reference"] = find_child(child,
                                                 ID_REFERENCE_BLOCK) is not None
                results.append((child, block, None))
        offset = child_end
    return results


# --------------------------------------------------------------------------- #
# track / metadata handling
# --------------------------------------------------------------------------- #

def _require_single(buf, top_elems, eid, label, fallback_offset):
    matches = [e for e in top_elems if e.eid == eid]
    if not matches:
        raise RepairError("missing required %s element" % label,
                          fallback_offset)
    elem = matches[0]
    elem.children, _ = scan_children(buf, elem.payload_offset,
                                     elem.payload_end)
    return elem


def parse_tracks(buf, tracks_elem):
    entries = []
    tracks_elem.children, _ = scan_children(buf,
                                            tracks_elem.payload_offset,
                                            tracks_elem.payload_end)
    for entry in find_children(tracks_elem, ID_TRACK_ENTRY):
        entry.children, _ = scan_children(buf, entry.payload_offset,
                                          entry.payload_end)
        number = child_uint(buf, entry, ID_TRACK_NUMBER)
        track_type = child_uint(buf, entry, ID_TRACK_TYPE)
        codec = child_text(buf, entry, ID_CODEC_ID)
        if number is None:
            raise RepairError("TrackEntry without TrackNumber",
                              entry.header_offset)
        if not codec:
            raise RepairError("track %d missing CodecID" % number,
                              entry.header_offset)
        private = find_child(entry, ID_CODEC_PRIVATE)
        needs_private = codec.startswith(("V_MPEG4/", "V_VP9", "A_OPUS",
                                          "A_AAC", "S_TEXT/ASS"))
        if private is None and needs_private:
            raise RepairError(
                "track %d (%s) missing required CodecPrivate"
                % (number, codec), entry.header_offset)
        lacing_flag = child_uint(buf, entry, ID_FLAG_LACING, default=1)
        entries.append({
            "number": number,
            "type": track_type,
            "codec": codec,
            "lacing": lacing_flag,
            "header_offset": entry.header_offset,
            "video": find_child(entry, ID_VIDEO) is not None,
            "audio": find_child(entry, ID_AUDIO) is not None,
            "kept_codec_private": private is not None,
        })
    if not entries:
        raise RepairError("Tracks element contains no TrackEntry",
                          tracks_elem.header_offset)
    return entries


# --------------------------------------------------------------------------- #
# rebuilding
# --------------------------------------------------------------------------- #

def _element_bytes(buf, elem):
    """Original on-disk bytes of a complete element."""
    if elem.unknown_size:
        raise RepairError("cannot copy unknown-size element verbatim",
                          elem.header_offset)
    return buf[elem.header_offset:elem.payload_end]


def build_cues(cue_records):
    """cue_records: sorted list of dicts(time, track, cluster_rel, block_rel)."""
    payload = bytearray()
    for record in cue_records:
        point = bytearray()
        time_bytes = encode_uint(record["time"])
        point += encode_element_id(ID_CUE_TIME)
        point += encode_vint(len(time_bytes)) + time_bytes
        pos = bytearray()
        pos += encode_element_id(ID_CUE_TRACK) + encode_vint(1)
        pos += encode_uint(record["track"])
        cluster_bytes = encode_uint(record["cluster_rel"])
        pos += encode_element_id(ID_CUE_CLUSTER_POSITION)
        pos += encode_vint(len(cluster_bytes)) + cluster_bytes
        rel_bytes = encode_uint(record["block_rel"])
        pos += encode_element_id(ID_CUE_RELATIVE_POSITION)
        pos += encode_vint(len(rel_bytes)) + rel_bytes
        point += encode_element_id(ID_CUE_TRACK_POSITIONS)
        point += encode_vint(len(pos)) + bytes(pos)
        payload += encode_element_id(ID_CUE_POINT)
        payload += encode_vint(len(point)) + bytes(point)
    return encode_element_id(ID_CUES) + encode_vint(len(payload)) + \
        bytes(payload)


def build_seek_head(entries):
    """entries: iterable of (element_id, relative_position)."""
    body = bytearray()
    for eid, position in entries:
        entry = bytearray()
        entry += encode_element_id(ID_SEEK_ID)
        raw_id = encode_element_id(eid)
        entry += encode_vint(len(raw_id)) + raw_id
        pos_bytes = encode_uint(position)
        entry += encode_element_id(ID_SEEK_POSITION)
        entry += encode_vint(len(pos_bytes)) + pos_bytes
        body += encode_element_id(ID_SEEK) + encode_vint(len(entry))
        body += bytes(entry)
    return encode_element_id(ID_SEEK_HEAD) + encode_vint(len(body)) + \
        bytes(body)


def _frame_hash(buf, block):
    digest = hashlib.sha256()
    for off, length in block["frames"]:
        digest.update(buf[off:off + length])
    return digest.hexdigest()


def repair(source_bytes):
    """Return (output_bytes, report_dict) for the given MKV byte string."""
    try:
        return _repair(source_bytes)
    except RepairError:
        raise
    except (ebml.EBMLSyntaxError, Truncated, ValueError, KeyError,
            IndexError) as exc:
        # Any malformed structure must surface as a locatable error rather
        # than a traceback.
        offset = getattr(exc, "offset", 0)
        detail = str(exc)
        prefix = "at byte %d: " % offset
        if detail.startswith(prefix):
            detail = detail[len(prefix):]
        raise RepairError("malformed Matroska data: %s" % detail, offset)


def _repair(source_bytes):
    buf = source_bytes
    if len(buf) < 4 or buf[:4] != b"\x1a\x45\xdf\xa3":
        raise RepairError("not an EBML file (EBML header id missing)", 0)
    # Locate header/segment manually so an unknown segment size is accepted.
    try:
        h_id_len, h_id = read_element_id(buf, 0)
        h_size_len, h_size = read_vint(buf, h_id_len, allow_unknown=True)
    except Truncated as exc:
        raise RepairError("EBML header truncated", exc.offset)
    if h_id != ID_EBML_HEADER:
        raise RepairError("first element is not EBML header", 0)
    if h_size is None:
        raise RepairError("EBML header uses unknown size", 0)
    header = Element(ID_EBML_HEADER, 0, h_id_len, h_size_len, h_size,
                     h_id_len + h_size_len, h_id_len + h_size_len + h_size)
    if header.payload_end > len(buf):
        raise RepairError("EBML header payload truncated",
                          header.payload_offset)
    seg_off = header.payload_end
    try:
        s_id_len, s_id = read_element_id(buf, seg_off)
        s_size_len, s_size = read_vint(buf, seg_off + s_id_len,
                                       allow_unknown=True)
    except Truncated as exc:
        raise RepairError("Segment header truncated", exc.offset)
    if s_id != ID_SEGMENT:
        raise RepairError("no Segment element found", seg_off)
    seg_payload_off = seg_off + s_id_len + s_size_len
    seg_payload_end = len(buf) if s_size is None else \
        min(len(buf), seg_payload_off + s_size)
    segment = Element(ID_SEGMENT, seg_off, s_id_len, s_size_len, s_size,
                      seg_payload_off, seg_payload_end,
                      unknown_size=s_size is None)

    level = scan_segment_level(buf, segment.payload_offset,
                               segment.payload_end)
    top_elems = [item["elem"] for item in level if "elem" in item]

    info_elem = _require_single(buf, top_elems, ID_INFO, "Info",
                                segment.payload_offset)
    tracks_elem = _require_single(buf, top_elems, ID_TRACKS, "Tracks",
                                  segment.payload_offset)
    tracks = parse_tracks(buf, tracks_elem)
    known_tracks = {t["number"] for t in tracks}
    time_scale = child_uint(buf, info_elem, ID_TIMESTAMP_SCALE, default=1000000)

    # Copy Info/Tracks verbatim; drop original SeekHead/Cues/Void/CRC.
    info_bytes = _element_bytes(buf, info_elem)
    tracks_bytes = _element_bytes(buf, tracks_elem)

    recovered = []
    discarded = []
    cluster_outputs = []
    cue_records = []
    video_tracks = {t["number"] for t in tracks if t["video"]}

    for item in level:
        if item["kind"] not in ("cluster", "cluster_unknown", "truncated"):
            continue
        if item["kind"] == "truncated_header":
            discarded.append({
                "offset": item["offset"], "length": item["length"],
                "reason": "truncated element header at segment tail",
            })
            continue
        elem = item["elem"]
        # Locate the mandatory Cluster Timecode (0xE7); a CRC-32 (0xBF) may
        # precede it.  We walk element headers without trusting payloads.
        tc_elem = None
        probe = elem.payload_offset
        while probe < elem.payload_end:
            try:
                c_id_len, c_id = read_element_id(buf, probe)
                c_size_len, c_size = read_vint(buf, probe + c_id_len,
                                               allow_unknown=True)
            except Truncated:
                break
            c_payload = probe + c_id_len + c_size_len
            c_end = elem.payload_end if c_size is None else \
                c_payload + c_size
            if c_end > elem.payload_end:
                break
            if c_id == ID_TIMESTAMP and c_size:
                tc_elem = Element(ID_TIMESTAMP, probe, c_id_len, c_size_len,
                                  c_size, c_payload, c_end)
                break
            if c_id == ID_SIMPLE_BLOCK or c_id == ID_BLOCK_GROUP:
                break                              # blocks before TC: invalid
            probe = c_end
        if tc_elem is None:
            discarded.append({
                "offset": elem.header_offset,
                "length": elem.payload_end - elem.header_offset,
                "reason": "cluster without readable Timecode",
            })
            continue
        cluster_tc = read_uint(buf, tc_elem.payload_offset,
                               tc_elem.payload_end - tc_elem.payload_offset)
        walked = _walk_cluster_children(buf, elem, cluster_tc)
        kept_children = [tc_elem]
        cluster_had_loss = elem.truncated or elem.unknown_size
        for child, block, drop in walked:
            if drop is not None:
                discarded.append(drop)
                cluster_had_loss = True
                continue
            if child.eid == ID_CRC_32 or child.eid == ID_TIMESTAMP:
                # CRC becomes invalid after rebuild; TC already prepended.
                if child.eid == ID_CRC_32:
                    cluster_had_loss = True
                continue
            if block is None:
                # Non-block, non-CRC cluster children (e.g. Position, PrevSize)
                # are retained verbatim when complete.
                kept_children.append(child)
                continue
            if block["track"] not in known_tracks:
                discarded.append({
                    "offset": child.header_offset,
                    "length": child.payload_end - child.header_offset,
                    "reason": "block references unknown track %d"
                              % block["track"],
                })
                cluster_had_loss = True
                continue
            kept_children.append(child)
            recovered.append({
                "source_offset": block["raw_header_offset"],
                "payload_offset": block["raw_payload_offset"],
                "payload_length": block["raw_payload_length"],
                "track": block["track"],
                "cluster_timecode": cluster_tc,
                "relative_time": block["relative_time"],
                "display_time_ms": round(
                    block["display_time"] * time_scale / 1_000_000.0, 3),
                "lacing": block["lacing"],
                "frames": len(block["frames"]),
                "frame_sizes": [length for _, length in block["frames"]],
                "sha256": _frame_hash(buf, block),
                "container": "SimpleBlock" if child.eid == ID_SIMPLE_BLOCK
                else "BlockGroup",
            })
            if (block["track"] in video_tracks and block["keyframe"]
                    and child.eid == ID_SIMPLE_BLOCK):
                cue_records.append({
                    "time": block["display_time"],
                    "track": block["track"],
                    "cluster_rel": 0,      # patched after layout
                    "block_rel": 0,
                    "cluster_index": len(cluster_outputs),
                    "block_header_source": child.header_offset,
                })
            elif (block["track"] in video_tracks and child.eid == ID_BLOCK_GROUP
                  and not block["reference"]):
                cue_records.append({
                    "time": block["display_time"],
                    "track": block["track"],
                    "cluster_rel": 0,
                    "block_rel": 0,
                    "cluster_index": len(cluster_outputs),
                    "block_header_source": child.header_offset,
                })
        if len(kept_children) <= 1:
            discarded.append({
                "offset": elem.header_offset,
                "length": elem.payload_end - elem.header_offset,
                "reason": "cluster contained no recoverable blocks",
            })
            continue
        body = bytearray()
        block_rel_map = {}
        for child in kept_children:
            if child.unknown_size:
                raise RepairError("child still has unknown size",
                                  child.header_offset)
            if child.eid in (ID_SIMPLE_BLOCK, ID_BLOCK_GROUP):
                # CueRelativePosition is relative to the Cluster element
                # itself, i.e. includes the rebuilt Cluster header; its size
                # is fixed once the body length is known below.
                block_rel_map[child.header_offset] = len(body)
            body += _element_bytes(buf, child)
        cluster_header = encode_element_id(ID_CLUSTER) + \
            encode_vint(len(body))
        # Shift recorded block positions by the now-known header length.
        header_len = len(cluster_header)
        block_rel_map = {src: header_len + rel
                         for src, rel in block_rel_map.items()}
        cluster_bytes = bytes(cluster_header) + bytes(body)
        cluster_outputs.append({
            "bytes": cluster_bytes,
            "source_offset": elem.header_offset,
            "unknown_size": elem.unknown_size,
            "truncated": elem.truncated,
            "had_loss": cluster_had_loss,
            "block_rel_map": block_rel_map,
        })

    if not cluster_outputs:
        # Report the furthest byte the scanner could make sense of, so the
        # error points at where recoverable data ran out rather than at 0.
        tail = max((item.get("elem").payload_end
                    for item in level if item.get("elem")), default=None)
        raise RepairError(
            "no complete Cluster could be recovered before end of data",
            tail if tail is not None else segment.payload_offset)

    # ---- layout: positions relative to the output Segment payload ----------
    def layout(seek_head_bytes):
        pos = 0
        positions = {}
        order = [(ID_SEEK_HEAD, seek_head_bytes), (ID_INFO, info_bytes),
                 (ID_TRACKS, tracks_bytes)]
        for eid, blob in order:
            positions[eid] = pos
            pos += len(blob)
        cluster_positions = []
        for idx, cluster in enumerate(cluster_outputs):
            cluster_positions.append(pos)
            pos += len(cluster["bytes"])
        cues_pos = pos
        return positions, cluster_positions, cues_pos

    # Fixed point: SeekHead size depends on positions it contains.
    seek_head = build_seek_head([])
    for _ in range(8):
        positions, cluster_pos, cues_pos = layout(seek_head)
        entries = [
            (ID_INFO, positions[ID_INFO]),
            (ID_TRACKS, positions[ID_TRACKS]),
            (ID_CUES, cues_pos),
        ]
        new_head = build_seek_head(entries)
        if len(new_head) == len(seek_head):
            seek_head = new_head
            break
        seek_head = new_head
    positions, cluster_pos, cues_pos = layout(seek_head)

    # Patch cue positions.
    for record in cue_records:
        cidx = record["cluster_index"]
        record["cluster_rel"] = cluster_pos[cidx]
        record["block_rel"] = cluster_outputs[cidx]["block_rel_map"].get(
            record["block_header_source"], 0)
    cues_bytes = build_cues(cue_records)

    segment_payload = bytearray()
    segment_payload += seek_head + info_bytes + tracks_bytes
    for cluster in cluster_outputs:
        segment_payload += cluster["bytes"]
    segment_payload += cues_bytes

    segment_header = encode_element_id(ID_SEGMENT) + \
        encode_vint(len(segment_payload))
    output = bytearray()
    output += _element_bytes(buf, header)
    output += segment_header
    output += segment_payload

    report = {
        "source_size": len(buf),
        "output_size": len(output),
        "segment_payload_position": len(_element_bytes(buf, header)) +
        len(segment_header),
        "timestamp_scale": time_scale,
        "tracks": [
            {
                "number": t["number"], "type": t["type"],
                "codec": t["codec"], "lacing": bool(t["lacing"]),
                "source_offset": t["header_offset"],
                "kept_codec_private": t["kept_codec_private"],
            } for t in tracks
        ],
        "clusters": len(cluster_outputs),
        "cue_points": len(cue_records),
        "recovered": recovered,
        "discarded": discarded,
    }
    return bytes(output), report


def write_report(report, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
