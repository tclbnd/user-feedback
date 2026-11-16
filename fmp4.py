#!/usr/bin/env python3
"""
fmp4.py - fragmented MP4 (ISO/IEC 14496-12) per-track sample indexer.

Supported box subset:
  ftyp, moov > trak > tkhd / mdia > mdhd, hdlr
       > mvex > trex
  moof > mfhd, traf > tfhd, tfdt, trun (v0 unsigned / v1 signed cts)
  mdat

Design properties required by the task:
  * Box reading is bounds-checked and offset based: 32-bit size, 64-bit
    largesize (size==1) and box-to-EOF (size==0) headers are all handled,
    unknown boxes are skipped by their declared size, and a parser never
    reads past a container's end.
  * Every trun carries its own data_offset anchored on the tfhd base, so
    the second run of a fragment (e.g. audio behind video) is never
    located through the first run's offset.
  * Per-sample fields fall back to tfhd defaults and then trex defaults.
  * DTS comes from tfdt + cumulative durations across all runs of a traf;
    PTS = DTS + signed composition offset, so B-frame style reorder is
    reflected in the presentation timeline instead of decode order.
  * Sample payloads are located by absolute byte ranges and re-read via
    seek(); indexing does not depend on how the transport chunks reads.
  * A truncated mdat produces locatable errors while every complete track
    and fragment remains indexed.

CLI: python3 fmp4.py FILE [OUT.json]
"""
import hashlib
import json
import struct
import sys

# sample_is_non_sync_sample is bit 16 of the 32-bit sample flags word.
NON_SYNC_FLAG = 0x00010000

CONTAINER_BOXES = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex",
    b"moof", b"traf", b"edts", b"udta",
}


class Fmp4Error(Exception):
    """Structural parse failure with enough context to locate it."""

    def __init__(self, code, message, **loc):
        super().__init__(message)
        self.code = code
        self.loc = loc


class Box:
    __slots__ = ("type", "start", "header_end", "end")

    def __init__(self, type_, start, header_end, end):
        self.type = type_
        self.start = start            # first header byte (absolute)
        self.header_end = header_end  # first payload byte (absolute)
        self.end = end                # one past last box byte (absolute)

    def as_dict(self, depth):
        return {
            "type": self.type.decode("latin1"),
            "depth": depth,
            "start": self.start,
            "header_end": self.header_end,
            "end": self.end,
            "size": self.end - self.start,
        }


class ChunkedReader:
    """File-like object whose read() never returns more than chunk bytes.

    The indexer only relies on seek()/tell() and short-read tolerant reads,
    so feeding it through this wrapper with varying chunk sizes must yield
    exactly the same index as reading the whole file at once.
    """

    def __init__(self, stream, chunk):
        self._s = stream
        self.chunk = chunk
        self.max_read = 0

    def seek(self, pos, whence=0):
        return self._s.seek(pos, whence)

    def tell(self):
        return self._s.tell()

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.chunk
        n = min(n, self.chunk)
        data = self._s.read(n)
        if data:
            self.max_read = max(self.max_read, len(data))
        return data

    def close(self):
        return self._s.close()


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def read_exact(stream, n):
    """Short-read tolerant read: loops until n bytes or EOF."""
    parts = []
    remaining = n
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def u8(stream):
    return read_exact(stream, 1)[0]


def u16(stream):
    return struct.unpack(">H", read_exact(stream, 2))[0]


def u32(stream):
    return struct.unpack(">I", read_exact(stream, 4))[0]


def u64(stream):
    return struct.unpack(">Q", read_exact(stream, 8))[0]


def i32(stream):
    return struct.unpack(">i", read_exact(stream, 4))[0]


def fullbox(stream, payload_start):
    stream.seek(payload_start)
    head = read_exact(stream, 4)
    version = head[0]
    flags = (head[1] << 16) | (head[2] << 8) | head[3]
    return version, flags, payload_start + 4


def iter_boxes(stream, start, end, tolerate_truncation=False):
    """Yield every direct child Box inside [start, end), bounds checked.

    With tolerate_truncation=True (top-level scan only), a final box whose
    declared size runs past EOF is clipped to EOF instead of raising, so a
    cut mdat still produces a locatable truncated_mdat sample error.
    """
    pos = start
    while pos < end:
        stream.seek(pos)
        head = read_exact(stream, 8)
        if not head:
            return
        if len(head) < 8:
            if tolerate_truncation:
                raise Fmp4Error(
                    "truncated_box", "final box header is shorter than 8 bytes",
                    offset=pos, available=len(head))
            raise Fmp4Error(
                "truncated_box", "box header is shorter than 8 bytes",
                offset=pos, available=len(head))
        size = struct.unpack(">I", head[:4])[0]
        btype = head[4:8]
        header_end = pos + 8
        if size == 1:  # 64-bit largesize
            ext = read_exact(stream, 8)
            if len(ext) < 8:
                raise Fmp4Error(
                    "truncated_box", "64-bit box size is incomplete",
                    offset=pos, available=len(head) + len(ext))
            size = struct.unpack(">Q", ext)[0]
            header_end += 8
        elif size == 0:  # box extends to end of enclosing scope
            size = end - pos
        elif size < 8:
            raise Fmp4Error(
                "invalid_box_size",
                "32-bit box size must be 0, 1 or at least 8",
                offset=pos, declared_size=size)
        box_end = pos + size
        if size < header_end - pos:
            raise Fmp4Error(
                "invalid_box_size", "declared box size is smaller than header",
                offset=pos, declared_size=size)
        clipped = False
        if box_end > end:
            if not tolerate_truncation:
                raise Fmp4Error(
                    "truncated_box", "declared box runs past enclosing scope",
                    type=btype.decode("latin1"), offset=pos,
                    declared_end=box_end, scope_end=end)
            clipped = True
            box_end = end
        yield Box(btype, pos, header_end, box_end)
        if clipped:
            return
        pos = box_end


def sha256_range(stream, offset, size):
    h = hashlib.sha256()
    stream.seek(offset)
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1 << 16))
        if not chunk:
            raise Fmp4Error(
                "truncated_mdat", "payload ends while hashing sample",
                offset=stream.tell(), missing=remaining)
        h.update(chunk)
        remaining -= len(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# moov level
# --------------------------------------------------------------------------

def parse_trak(box, stream):
    track = {"id": None, "timescale": None, "handler": None}
    for child in iter_boxes(stream, box.header_end, box.end):
        if child.type == b"tkhd":
            version, _, cur = fullbox(stream, child.header_end)
            stream.seek(cur)
            if version == 1:
                read_exact(stream, 16)  # creation/modification u64 x2
            else:
                read_exact(stream, 8)   # u32 x2
            track["id"] = u32(stream)
        elif child.type == b"mdia":
            for md in iter_boxes(stream, child.header_end, child.end):
                if md.type == b"mdhd":
                    version, _, cur = fullbox(stream, md.header_end)
                    stream.seek(cur)
                    if version == 1:
                        read_exact(stream, 16)
                    else:
                        read_exact(stream, 8)
                    track["timescale"] = u32(stream)
                elif md.type == b"hdlr":
                    _, _, cur = fullbox(stream, md.header_end)
                    stream.seek(cur)
                    read_exact(stream, 4)  # pre_defined
                    track["handler"] = read_exact(stream, 4).decode("latin1")
    return track


def parse_moov(box, stream):
    tracks, trex = {}, {}
    for child in iter_boxes(stream, box.header_end, box.end):
        if child.type == b"trak":
            t = parse_trak(child, stream)
            if t["id"] is not None:
                tracks[t["id"]] = t
        elif child.type == b"mvex":
            for ex in iter_boxes(stream, child.header_end, child.end):
                if ex.type != b"trex":
                    continue
                _, _, cur = fullbox(stream, ex.header_end)
                stream.seek(cur)
                tid = u32(stream)
                trex[tid] = {
                    "sample_description_index": u32(stream),
                    "duration": u32(stream),
                    "size": u32(stream),
                    "flags": u32(stream),
                }
    return tracks, trex


# --------------------------------------------------------------------------
# moof level
# --------------------------------------------------------------------------

def parse_tfhd(box, stream):
    version, flags, cur = fullbox(stream, box.header_end)
    stream.seek(cur)
    tfhd = {
        "track_id": u32(stream),
        "base_data_offset": None,
        "default_description_index": None,
        "default_duration": None,
        "default_size": None,
        "default_flags": None,
        "default_base_is_moof": bool(flags & 0x020000),
        "duration_is_empty": bool(flags & 0x010000),
    }
    if flags & 0x000001:
        tfhd["base_data_offset"] = u64(stream)
    if flags & 0x000002:
        tfhd["default_description_index"] = u32(stream)
    if flags & 0x000008:
        tfhd["default_duration"] = u32(stream)
    if flags & 0x000010:
        tfhd["default_size"] = u32(stream)
    if flags & 0x000020:
        tfhd["default_flags"] = u32(stream)
    return tfhd


def parse_tfdt(box, stream):
    version, _, cur = fullbox(stream, box.header_end)
    stream.seek(cur)
    return u64(stream) if version == 1 else u32(stream)


def parse_trun(box, stream):
    version, flags, cur = fullbox(stream, box.header_end)
    stream.seek(cur)
    count = u32(stream)
    run = {
        "version": version, "flags": flags, "count": count,
        "data_offset": 0, "first_sample_flags": None, "samples": [],
    }
    if flags & 0x000001:
        run["data_offset"] = i32(stream)
    if flags & 0x000004:
        run["first_sample_flags"] = u32(stream)
    for _ in range(count):
        s = {}
        if flags & 0x000100:
            s["duration"] = u32(stream)
        if flags & 0x000200:
            s["size"] = u32(stream)
        if flags & 0x000400:
            s["sample_flags"] = u32(stream)
        if flags & 0x000800:
            # v0 composition offsets are unsigned, v1 are signed.
            s["cts"] = i32(stream) if version == 1 else u32(stream)
        run["samples"].append(s)
    return run


def parse_moof(box, stream):
    """Return (sequence_number, raw trafs) without touching mdat bytes."""
    seq = None
    trafs = []
    for child in iter_boxes(stream, box.header_end, box.end):
        if child.type == b"mfhd":
            _, _, cur = fullbox(stream, child.header_end)
            stream.seek(cur)
            seq = u32(stream)
        elif child.type == b"traf":
            tfhd = tfdt = None
            truns = []
            for tb in iter_boxes(stream, child.header_end, child.end):
                if tb.type == b"tfhd":
                    tfhd = parse_tfhd(tb, stream)
                elif tb.type == b"tfdt":
                    tfdt = parse_tfdt(tb, stream)
                elif tb.type == b"trun":
                    truns.append(parse_trun(tb, stream))
            if tfhd is not None:
                trafs.append({"tfhd": tfhd, "tfdt": tfdt or 0,
                              "truns": truns, "box": child})
    return seq, trafs


# --------------------------------------------------------------------------
# index build
# --------------------------------------------------------------------------

def _err(errors, code, message, **loc):
    entry = {"code": code, "message": message}
    entry.update(loc)
    errors.append(entry)
    return entry


def _inside_mdat(offset, end, mdats):
    return any(start <= offset and end <= stop for start, stop in mdats)


def _build_traf_samples(moof, traf, seq, trex, tracks, mdats, file_end,
                        stream, errors):
    tfhd = traf["tfhd"]
    tid = tfhd["track_id"]
    defaults = trex.get(tid, {})

    def default(name, fallback=0):
        val = tfhd.get("default_" + name)
        if val is not None:
            return val
        return defaults.get(name, fallback)

    # Data-offset base resolution (ISO 14496-12 8.8.7/8.8.8):
    # explicit base-data-offset wins; default-base-is-moof anchors on the
    # moof header; otherwise the de-facto default is the moof origin too.
    if tfhd["base_data_offset"] is not None:
        base = tfhd["base_data_offset"]
    else:
        base = moof.start

    if tfhd["duration_is_empty"]:
        # duration-is-empty: the traf declares no samples
        return tid, []

    dts = traf["tfdt"]
    out = []
    for ri, run in enumerate(traf["truns"]):
        # trun data_offset is already relative to the tfhd base; do not add
        # moof.start again (that bug makes every run except the first land
        # in the wrong track's bytes).
        offset = base + run["data_offset"]
        for si, raw in enumerate(run["samples"]):
            duration = raw.get("duration", default("duration"))
            size = raw.get("size", default("size"))
            if "sample_flags" in raw:
                sflags = raw["sample_flags"]
            elif si == 0 and run["first_sample_flags"] is not None:
                sflags = run["first_sample_flags"]
            else:
                sflags = default("flags")
            cts = raw.get("cts", 0)

            if size <= 0:
                _err(errors, "unresolved_sample_size",
                     "sample size is neither in trun nor in defaults",
                     track_id=tid, fragment_seq=seq, trun=ri, sample=si,
                     offset=offset)
                break
            end = offset + size
            if end > file_end:
                _err(errors, "truncated_mdat",
                     "sample payload extends past end of file",
                     track_id=tid, fragment_seq=seq, trun=ri, sample=si,
                     offset=offset, requested_end=end,
                     available=file_end, missing=end - file_end)
                break
            if not _inside_mdat(offset, end, mdats):
                _err(errors, "sample_outside_mdat",
                     "sample byte range is not covered by any mdat box",
                     track_id=tid, fragment_seq=seq, trun=ri, sample=si,
                     offset=offset, requested_end=end)
                # following offsets would shift, so stop this run
                break

            pts = dts + cts
            sample = {
                "track_id": tid,
                "fragment_seq": seq,
                "trun": ri,
                "sample": si,
                "offset": offset,
                "size": size,
                "end": end,
                "dts": dts,
                "cts": cts,
                "pts": pts,
                "duration": duration,
                "flags": sflags,
                "keyframe": (sflags & NON_SYNC_FLAG) == 0,
                "sha256": sha256_range(stream, offset, size),
                "cont": None,  # filled in during continuation pass
            }
            out.append(sample)
            offset = end
            dts += duration
    return tid, out


def index_stream(stream, name="<stream>"):
    stream.seek(0, 2)
    file_end = stream.tell()
    stream.seek(0)

    errors = []
    tracks = {}
    trex = {}
    mdats = []          # (payload_start, payload_end)
    box_layout = []     # flattened box tree for byte-level acceptance
    per_track = {}      # tid -> {"samples": [], "fragments": []}

    def layout(box, depth):
        box_layout.append(box.as_dict(depth))

    def ensure_track(tid):
        if tid not in per_track:
            meta = tracks.get(tid, {"id": tid, "timescale": None,
                                    "handler": None})
            per_track[tid] = {"meta": meta, "samples": [], "fragments": []}
        return per_track[tid]

    try:
        top = list(iter_boxes(stream, 0, file_end,
                              tolerate_truncation=True))
    except Fmp4Error as e:
        entry = _err(errors, e.code, str(e), **e.loc)
        entry["offset"] = e.loc.get("offset", 0)
        top = []

    def flatten(box, depth):
        """Depth-first layout walk; a clipped container still lists itself."""
        layout(box, depth)
        if box.type not in CONTAINER_BOXES or depth >= 2:
            return
        try:
            children = list(iter_boxes(stream, box.header_end, box.end))
        except Fmp4Error as e:
            _err(errors, e.code, str(e), parent=box.type.decode("latin1"),
                 parent_offset=box.start, **e.loc)
            return
        for child in children:
            flatten(child, depth + 1)

    # Pass 1: layout tree + moov metadata + every mdat payload window,
    # so sample validation in pass 2 never depends on moof/mdat ordering.
    for box in top:
        flatten(box, 0)
        if box.type == b"moov":
            try:
                t2, d2 = parse_moov(box, stream)
                tracks.update(t2)
                trex.update(d2)
            except Fmp4Error as e:
                _err(errors, e.code, str(e), **e.loc)
        elif box.type == b"mdat":
            # box.end may have been clipped to EOF for a truncated fragment
            mdats.append((box.header_end, min(box.end, file_end)))

    # Pass 2: moof -> traf -> trun sample resolution.
    for box in top:
        if box.type != b"moof":
            continue
        try:
            seq, trafs = parse_moof(box, stream)
        except Fmp4Error as e:
            _err(errors, e.code, str(e), moof_offset=box.start, **e.loc)
            continue
        for traf in trafs:
                try:
                    tid, samples = _build_traf_samples(
                        box, traf, seq, trex, tracks, mdats, file_end,
                        stream, errors)
                except Fmp4Error as e:
                    _err(errors, e.code, str(e), moof_offset=box.start,
                         **e.loc)
                    continue
                bucket = ensure_track(tid)
                if samples:
                    first = samples[0]
                    last = samples[-1]
                    expected = None
                    if bucket["fragments"]:
                        prev = bucket["fragments"][-1]
                        expected = prev["last_dts"] + prev["last_duration"]
                    cont = None if expected is None else first["dts"] == expected
                    bucket["fragments"].append({
                        "seq": seq,
                        "moof_offset": box.start,
                        "first_dts": first["dts"],
                        "last_dts": last["dts"],
                        "last_duration": last["duration"],
                        "expected_dts": expected,
                        "cont": cont,
                        "gap": None if expected is None
                        else first["dts"] - expected,
                        "sample_count": len(samples),
                        "trun_count": len(traf["truns"]),
                    })
                    first["cont"] = cont
                    bucket["samples"].extend(samples)

    out_tracks = []
    for tid in sorted(per_track):
        bucket = per_track[tid]
        meta = bucket["meta"]
        ts = meta.get("timescale")
        decode_order = bucket["samples"]  # already in file/DTS order
        timeline = sorted(decode_order, key=lambda s: (s["pts"], s["dts"]))

        def ms(t):
            return round(t * 1000.0 / ts, 3) if ts else None

        for s in decode_order:
            s["dts_ms"] = ms(s["dts"])
            s["pts_ms"] = ms(s["pts"])
        out_tracks.append({
            "id": tid,
            "handler": meta.get("handler"),
            "timescale": ts,
            "trex_defaults": trex.get(tid),
            "fragments": bucket["fragments"],
            "decode_order": decode_order,
            "timeline": timeline,
        })

    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(1 << 16)
        if not chunk:
            break
        digest.update(chunk)

    return {
        "file": name,
        "size": file_end,
        "sha256": digest.hexdigest(),
        "boxes": box_layout,
        "mdat": [{"payload_start": a, "payload_end": b} for a, b in mdats],
        "tracks": out_tracks,
        "errors": errors,
    }


def index_bytes(data, name="<bytes>"):
    import io
    return index_stream(io.BytesIO(data), name)


def index_path(path):
    with open(path, "rb") as f:
        return index_stream(f, path)


def read_sample(stream, sample):
    """Random-access payload read; works on a freshly opened file."""
    stream.seek(sample["offset"])
    data = read_exact(stream, sample["size"])
    if len(data) != sample["size"]:
        raise Fmp4Error(
            "truncated_mdat", "short payload read",
            offset=sample["offset"], expected=sample["size"], got=len(data))
    return data


def main(argv):
    if len(argv) < 2:
        print("usage: python3 fmp4.py FILE [OUT.json]", file=sys.stderr)
        return 2
    result = index_path(argv[1])
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if len(argv) >= 3:
        with open(argv[2], "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
