"""Repair truncated Matroska (.mkv) files.

Scans the EBML structure of a file whose tail was cut off, salvages every
complete SimpleBlock/BlockGroup (Xiph, fixed and EBML lacing supported),
rewrites the Segment with a fresh SeekHead and video keyframe Cues, and emits
a JSON report listing recovered/discarded blocks with original offsets.

Usage: python3 repair_mkv.py source.mkv output.mkv report.json
Pure standard library, Python 3.9+.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

# ---- element IDs ----
ID_EBML = 0x1A45DFA3
ID_SEGMENT = 0x18538067
ID_SEEKHEAD = 0x114D9B74
ID_SEEK = 0x4DBB
ID_SEEKID = 0x53AB
ID_SEEKPOS = 0x53AC
ID_INFO = 0x1549A966
ID_TIMESTAMPSCALE = 0x2AD7B1
ID_TRACKS = 0x1654AE6B
ID_TRACKENTRY = 0xAE
ID_TRACKNUMBER = 0xD7
ID_TRACKTYPE = 0x83
ID_CODECID = 0x86
ID_CLUSTER = 0x1F43B675
ID_TIMESTAMP = 0xE7
ID_SIMPLEBLOCK = 0xA3
ID_BLOCKGROUP = 0xA0
ID_BLOCK = 0xA1
ID_BLOCKREFERENCE = 0xFB
ID_CUES = 0x1C53BB6B
ID_CUEPOINT = 0xBB
ID_CUETIME = 0xB3
ID_CUETRACKPOSITIONS = 0xB7
ID_CUETRACK = 0xF7
ID_CUECLUSTERPOSITION = 0xF1
ID_CRC32 = 0xBF
ID_VOID = 0xEC
ID_TAGS = 0x1254C367
ID_CHAPTERS = 0x1043A770
ID_ATTACHMENTS = 0x1941A469

TRACK_TYPE_VIDEO = 1
LACE_NAMES = {0: "none", 1: "xiph", 2: "fixed", 3: "ebml"}

ELEMENT_NAMES = {
    ID_EBML: "EBML", ID_SEGMENT: "Segment", ID_SEEKHEAD: "SeekHead",
    ID_INFO: "Info", ID_TRACKS: "Tracks", ID_CLUSTER: "Cluster",
    ID_TIMESTAMP: "Timestamp", ID_SIMPLEBLOCK: "SimpleBlock",
    ID_BLOCKGROUP: "BlockGroup", ID_BLOCK: "Block", ID_CUES: "Cues",
    ID_CRC32: "CRC-32", ID_VOID: "Void", ID_TAGS: "Tags",
    ID_CHAPTERS: "Chapters", ID_ATTACHMENTS: "Attachments",
}

# IDs that only appear as direct Segment children; used to find the end of an
# unknown-size Cluster.
TOP_LEVEL_IDS = {ID_SEGMENT, ID_SEEKHEAD, ID_INFO, ID_TRACKS, ID_CLUSTER,
                 ID_CUES, ID_TAGS, ID_CHAPTERS, ID_ATTACHMENTS}


class RepairError(Exception):
    """Fatal, locatable problem in the source file."""
    def __init__(self, message, offset):
        super().__init__(f"{message} (at byte offset {offset})")
        self.offset = offset


class ShortRead(Exception):
    def __init__(self, offset):
        super().__init__(f"short read at byte offset {offset}")
        self.offset = offset


# ---- EBML primitives ----

def read_id(data, off, end):
    if off >= end:
        raise ShortRead(off)
    b0 = data[off]
    n = 1
    while n <= 4 and not (b0 & (1 << (8 - n))):
        n += 1
    if n > 4:
        raise RepairError(f"invalid element id (leading byte 0x{b0:02x})", off)
    if off + n > end:
        raise ShortRead(off)
    return int.from_bytes(data[off:off + n], "big"), n


def read_size(data, off, end):
    """Return (value, length); value is None for unknown size."""
    if off >= end:
        raise ShortRead(off)
    b0 = data[off]
    n = 1
    while n <= 8 and not (b0 & (1 << (8 - n))):
        n += 1
    if n > 8:
        raise RepairError(f"invalid element size (leading byte 0x{b0:02x})", off)
    if off + n > end:
        raise ShortRead(off)
    v = int.from_bytes(data[off:off + n], "big") & ((1 << (7 * n)) - 1)
    return (None if v == (1 << (7 * n)) - 1 else v), n


def vint_value(buf, off):
    """VINT with marker stripped (track numbers, lace sizes)."""
    if off >= len(buf):
        raise ValueError("missing vint")
    b0 = buf[off]
    n = 1
    while n <= 8 and not (b0 & (1 << (8 - n))):
        n += 1
    if n > 8 or off + n > len(buf):
        raise ValueError("bad vint")
    return int.from_bytes(buf[off:off + n], "big") & ((1 << (7 * n)) - 1), n


def enc_id(eid):
    if eid <= 0xFF:
        return bytes([eid])
    if eid <= 0x3FFF:
        return eid.to_bytes(2, "big")
    if eid <= 0x1FFFFF:
        return eid.to_bytes(3, "big")
    return eid.to_bytes(4, "big")


def enc_size(n):
    for length in range(1, 9):
        if n < (1 << (7 * length)) - 1:
            return (n | (1 << (7 * length))).to_bytes(length, "big")
    raise ValueError("element too large")


def enc_uint(n):
    return n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")


def elem(eid, payload):
    return enc_id(eid) + enc_size(len(payload)) + payload


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# ---- lacing ----

def _xiph_frames(body):
    if not body:
        raise ValueError("empty xiph lace header")
    count = body[0] + 1
    sizes, i = [], 1
    for _ in range(count - 1):
        s = 0
        while True:
            if i >= len(body):
                raise ValueError("truncated xiph lace sizes")
            b = body[i]
            i += 1
            s += b
            if b != 255:
                break
        sizes.append(s)
    last = len(body) - i - sum(sizes)
    if last < 0:
        raise ValueError("xiph lace sizes exceed block body")
    sizes.append(last)
    frames, p = [], i
    for s in sizes:
        frames.append(body[p:p + s])
        p += s
    return frames


def _fixed_frames(body):
    if not body:
        raise ValueError("empty fixed lace header")
    count = body[0] + 1
    rest = body[1:]
    if len(rest) % count:
        raise ValueError("fixed lace body not divisible by frame count")
    k = len(rest) // count
    return [rest[i * k:(i + 1) * k] for i in range(count)]


def _ebml_frames(body):
    if not body:
        raise ValueError("empty ebml lace header")
    count = body[0] + 1
    first, n = vint_value(body, 1)
    i = 1 + n
    sizes, cur = [first], first
    for _ in range(count - 2):
        d, n = vint_value(body, i)
        d -= (1 << (7 * n - 1)) - 1  # signed vint bias
        i += n
        cur += d
        if cur < 0:
            raise ValueError("negative ebml lace size")
        sizes.append(cur)
    last = len(body) - i - sum(sizes)
    if last < 0:
        raise ValueError("ebml lace sizes exceed block body")
    sizes.append(last)
    frames, p = [], i
    for s in sizes:
        frames.append(body[p:p + s])
        p += s
    return frames


def parse_block(payload, offset):
    """Parse a SimpleBlock/Block payload. Returns a block info dict."""
    try:
        track, n = vint_value(payload, 0)
    except ValueError:
        raise RepairError("block with invalid track number", offset)
    if n + 3 > len(payload):
        raise RepairError("block header truncated", offset)
    rel = int.from_bytes(payload[n:n + 2], "big", signed=True)
    flags = payload[n + 2]
    lace = (flags >> 1) & 3
    body = payload[n + 3:]
    if lace == 0:
        frames = [body]
    elif lace == 1:
        frames = _xiph_frames(body)
    elif lace == 2:
        frames = _fixed_frames(body)
    else:
        frames = _ebml_frames(body)
    return {"track": track, "rel_time": rel, "keyframe": bool(flags & 0x80),
            "lacing": LACE_NAMES[lace], "frames": frames, "payload": payload}


def parse_blockgroup(payload, offset):
    """Extract the Block of a complete BlockGroup; None if unusable."""
    block, has_reference = None, False
    cur = 0
    try:
        while cur < len(payload):
            eid, il = read_id(payload, cur, len(payload))
            sz, sl = read_size(payload, cur + il, len(payload))
            if sz is None or cur + il + sl + sz > len(payload):
                return None
            body = payload[cur + il + sl:cur + il + sl + sz]
            if eid == ID_BLOCK:
                block = parse_block(body, offset)
            elif eid == ID_BLOCKREFERENCE:
                has_reference = True
            cur += il + sl + sz
    except (ShortRead, RepairError, ValueError):
        return None
    if block is None:
        return None
    # A BlockGroup Block is a keyframe unless a BlockReference says otherwise.
    block["keyframe"] = not has_reference
    return block


# ---- metadata extraction ----

def iter_children(buf):
    """Yield (eid, payload) for a complete master element payload."""
    cur = 0
    while cur < len(buf):
        eid, il = read_id(buf, cur, len(buf))
        sz, sl = read_size(buf, cur + il, len(buf))
        if sz is None or cur + il + sl + sz > len(buf):
            raise RepairError("corrupt child element", cur)
        yield eid, buf[cur + il + sl:cur + il + sl + sz]
        cur += il + sl + sz


def parse_info(payload):
    scale = 1_000_000
    for eid, body in iter_children(payload):
        if eid == ID_TIMESTAMPSCALE and body:
            scale = int.from_bytes(body, "big")
    return scale


def parse_tracks(payload):
    tracks = {}
    for eid, body in iter_children(payload):
        if eid != ID_TRACKENTRY:
            continue
        num = ttype = None
        codec = ""
        for ceid, cbody in iter_children(body):
            if ceid == ID_TRACKNUMBER:
                num = int.from_bytes(cbody, "big")
            elif ceid == ID_TRACKTYPE:
                ttype = int.from_bytes(cbody, "big")
            elif ceid == ID_CODECID:
                codec = cbody.decode("ascii", "replace")
        if num is not None:
            tracks[num] = {"type": ttype, "codec": codec}
    return tracks


# ---- cluster scanning ----

def scan_cluster(data, cstart, payload_off, size, eof, report):
    """Scan one Cluster. Returns (cluster_dict, next_cursor, truncated)."""
    unknown = size is None
    end = eof if unknown else min(payload_off + size, eof)
    truncated = (not unknown) and (payload_off + size > eof)
    cl = {"offset": cstart, "timestamp": 0, "children": [], "blocks": [],
          "discarded": 0, "unknown_size": unknown}
    cur = payload_off
    while cur < end:
        try:
            eid, il = read_id(data, cur, end)
        except ShortRead:
            report["discarded"].append({"offset": cur, "element": "?",
                                        "reason": "truncated element id",
                                        "available": end - cur})
            cl["discarded"] += 1
            break
        if unknown and eid in TOP_LEVEL_IDS:
            break  # end of unknown-size cluster
        try:
            csz, sl = read_size(data, cur + il, end)
        except ShortRead:
            report["discarded"].append({"offset": cur,
                                        "element": ELEMENT_NAMES.get(eid, hex(eid)),
                                        "reason": "truncated element size",
                                        "available": end - cur})
            cl["discarded"] += 1
            break
        hdr = il + sl
        if csz is None:
            report["discarded"].append({"offset": cur,
                                        "element": ELEMENT_NAMES.get(eid, hex(eid)),
                                        "reason": "unknown-size element inside cluster"})
            cl["discarded"] += 1
            break
        cend = cur + hdr + csz
        if cend > end:
            report["discarded"].append({
                "offset": cur, "element": ELEMENT_NAMES.get(eid, hex(eid)),
                "reason": "short read", "declared_size": csz,
                "available": max(0, end - cur - hdr)})
            cl["discarded"] += 1
            break
        raw = data[cur:cend]
        payload = data[cur + hdr:cend]
        is_crc = False
        if eid == ID_TIMESTAMP:
            cl["timestamp"] = int.from_bytes(payload, "big")
        elif eid == ID_SIMPLEBLOCK:
            try:
                blk = parse_block(payload, cur)
            except (RepairError, ValueError) as exc:
                report["discarded"].append({"offset": cur, "element": "SimpleBlock",
                                            "reason": f"unparseable block: {exc}"})
                cl["discarded"] += 1
                cur = cend
                continue
            blk["offset"] = cur
            cl["blocks"].append(blk)
        elif eid == ID_BLOCKGROUP:
            blk = parse_blockgroup(payload, cur)
            if blk is None:
                report["discarded"].append({"offset": cur, "element": "BlockGroup",
                                            "reason": "unparseable block group"})
                cl["discarded"] += 1
                cur = cend
                continue
            blk["offset"] = cur
            cl["blocks"].append(blk)
        elif eid == ID_CRC32:
            is_crc = True  # dropped if the cluster lost any child
        cl["children"].append((is_crc, raw))
        cur = cend
    if cl["discarded"]:
        # A cluster that lost data can no longer validate its CRC-32.
        cl["children"] = [c for c in cl["children"] if not c[0]]
    cl["children"] = [raw for _, raw in cl["children"]]
    if not any(raw[0:1] == b"\xe7" for raw in cl["children"]):
        # Timestamp is mandatory; insert one at the front if it was missing.
        cl["children"].insert(0, elem(ID_TIMESTAMP, enc_uint(cl["timestamp"])))
    next_cursor = cur if unknown else payload_off + size
    return cl, next_cursor, truncated


# ---- top-level repair ----

def _payload_of(raw):
    """Payload bytes of a complete element given as raw bytes."""
    eid, il = read_id(raw, 0, len(raw))
    sz, sl = read_size(raw, il, len(raw))
    return raw[il + sl:il + sl + sz]


def repair(data):
    report = {"recovered": [], "discarded": [], "clusters": [], "error": None}
    if not data:
        raise RepairError("empty file", 0)
    eid, il = read_id(data, 0, len(data))
    if eid != ID_EBML:
        raise RepairError("missing EBML header (not a Matroska file)", 0)
    sz, sl = read_size(data, il, len(data))
    if sz is None or il + sl + sz > len(data):
        raise RepairError("truncated EBML header", 0)
    ebml_raw = data[:il + sl + sz]
    cur = il + sl + sz
    try:
        eid, il = read_id(data, cur, len(data))
    except ShortRead:
        raise RepairError("missing Segment element", cur)
    if eid != ID_SEGMENT:
        raise RepairError("missing Segment element after EBML header", cur)
    sz, sl = read_size(data, cur + il, len(data))
    seg_payload = cur + il + sl
    seg_end = len(data) if sz is None else min(seg_payload + sz, len(data))

    info_raw = tracks_raw = None
    preserved = []          # (eid, raw) other complete top-level elements
    clusters = []
    cur = seg_payload
    while cur < seg_end:
        try:
            eid, il = read_id(data, cur, seg_end)
            esz, sl = read_size(data, cur + il, seg_end)
        except ShortRead as exc:
            report["discarded"].append({"offset": exc.offset, "element": "?",
                                        "reason": "truncated segment-level element header"})
            break
        hdr = il + sl
        if eid == ID_CLUSTER:
            cl, cur, truncated = scan_cluster(data, cur, cur + hdr, esz,
                                              len(data), report)
            clusters.append(cl)
            if truncated:
                break  # nothing reliable beyond a cut cluster
            continue
        if esz is None:
            report["discarded"].append({"offset": cur,
                                        "element": ELEMENT_NAMES.get(eid, hex(eid)),
                                        "reason": "unknown-size top-level element"})
            break
        eend = cur + hdr + esz
        if eend > seg_end:
            if eid in (ID_INFO, ID_TRACKS):
                raise RepairError(
                    f"truncated {ELEMENT_NAMES[eid]} element "
                    f"(declared size {esz}, only {max(0, seg_end - cur - hdr)} bytes available)",
                    cur)
            report["discarded"].append({"offset": cur,
                                        "element": ELEMENT_NAMES.get(eid, hex(eid)),
                                        "reason": "short read", "declared_size": esz,
                                        "available": max(0, seg_end - cur - hdr)})
            break
        raw = data[cur:eend]
        if eid == ID_INFO:
            info_raw = raw
        elif eid == ID_TRACKS:
            tracks_raw = raw
        elif eid in (ID_SEEKHEAD, ID_CUES, ID_VOID, ID_CRC32):
            pass  # stale index / padding: replaced by rebuilt index
        else:
            preserved.append((eid, raw))
        cur = eend

    if info_raw is None:
        raise RepairError("missing required Info element", seg_payload)
    if tracks_raw is None:
        raise RepairError("missing required Tracks element", seg_payload)

    scale = parse_info(_payload_of(info_raw))
    tracks = parse_tracks(_payload_of(tracks_raw))
    if not tracks:
        raise RepairError("Tracks element contains no TrackEntry", seg_payload)
    video_tracks = {n for n, t in tracks.items() if t["type"] == TRACK_TYPE_VIDEO}

    # recovered block list + cue points
    cue_points = []  # (timecode, track, cluster_index)
    for ci, cl in enumerate(clusters):
        report["clusters"].append({
            "offset": cl["offset"], "timestamp": cl["timestamp"],
            "unknown_size": cl["unknown_size"],
            "status": ("truncated" if cl["discarded"] else
                       ("unknown-size" if cl["unknown_size"] else "complete")),
            "blocks_recovered": len(cl["blocks"]),
            "blocks_discarded": cl["discarded"]})
        for blk in cl["blocks"]:
            abs_tc = cl["timestamp"] + blk["rel_time"]
            frames = [{"size": len(f), "sha256": sha256(f)} for f in blk["frames"]]
            report["recovered"].append({
                "offset": blk["offset"], "cluster_offset": cl["offset"],
                "track": blk["track"], "timecode": abs_tc,
                "time_ms": abs_tc * scale / 1e6,
                "keyframe": blk["keyframe"], "lacing": blk["lacing"],
                "frames": frames, "block_sha256": sha256(blk["payload"])})
            if blk["keyframe"] and blk["track"] in video_tracks and abs_tc >= 0:
                cue_points.append((abs_tc, blk["track"], ci))

    out = build_output(ebml_raw, info_raw, tracks_raw, preserved, clusters,
                       cue_points)
    report["timestamp_scale"] = scale
    report["tracks"] = [{"number": n, "type": t["type"], "codec": t["codec"]}
                        for n, t in sorted(tracks.items())]
    report["cues"] = len(cue_points)
    report["clusters_recovered"] = len(clusters)
    return out, report


def build_seekhead(entries):
    payload = b"".join(
        elem(ID_SEEK, elem(ID_SEEKID, enc_id(eid)) + elem(ID_SEEKPOS, enc_uint(pos)))
        for eid, pos in entries)
    return elem(ID_SEEKHEAD, payload)


def build_cues(cue_points, cluster_pos):
    points = b""
    for tc, track, ci in sorted(cue_points):
        ctp = elem(ID_CUETRACK, enc_uint(track)) + \
              elem(ID_CUECLUSTERPOSITION, enc_uint(cluster_pos[ci]))
        points += elem(ID_CUEPOINT, elem(ID_CUETIME, enc_uint(tc)) +
                       elem(ID_CUETRACKPOSITIONS, ctp))
    return elem(ID_CUES, points)


def build_output(ebml_raw, info_raw, tracks_raw, preserved, clusters, cue_points):
    cluster_elems = [elem(ID_CLUSTER, b"".join(cl["children"])) for cl in clusters]
    head = [(ID_INFO, info_raw), (ID_TRACKS, tracks_raw)] + preserved
    seekhead = b""
    cues = b""
    for _ in range(20):  # seekhead size / positions fixpoint
        pos = len(seekhead)
        head_pos = []
        for _, raw in head:
            head_pos.append(pos)
            pos += len(raw)
        cluster_pos = []
        for ce in cluster_elems:
            cluster_pos.append(pos)
            pos += len(ce)
        cues = build_cues(cue_points, cluster_pos)
        entries = [(eid, p) for (eid, _), p in zip(head, head_pos)]
        entries.append((ID_CUES, pos))
        new_seekhead = build_seekhead(entries)
        if new_seekhead == seekhead:
            break
        seekhead = new_seekhead
    payload = seekhead + b"".join(raw for _, raw in head) + \
              b"".join(cluster_elems) + cues
    return ebml_raw + elem(ID_SEGMENT, payload)


def main():
    ap = argparse.ArgumentParser(description="Repair a truncated Matroska file")
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("report", type=Path)
    a = ap.parse_args()
    data = a.source.read_bytes()
    try:
        out, report = repair(data)
    except RepairError as exc:
        report = {"error": str(exc), "error_offset": exc.offset,
                  "recovered": [], "discarded": []}
        a.report.write_text(json.dumps(report, indent=1) + "\n")
        print(f"repair_mkv: {exc}", file=sys.stderr)
        return 2
    a.output.write_bytes(out)
    a.report.write_text(json.dumps(report, indent=1) + "\n")
    print(f"recovered {len(report['recovered'])} blocks, "
          f"discarded {len(report['discarded'])}, cues {report['cues']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
