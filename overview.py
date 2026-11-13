"""Multilevel waveform index with a non-destructive EDL.

Storage layout under ROOT:
  meta.json                 source identity (path, size, mtime, sha256, ...)
  source.idx                paged multilevel index over the raw source PCM
  edl/edl-N.json            immutable, atomically published EDL versions
  edl/index-N.idx           derived paged index for EDL version N
  current.txt               latest published version number
  stats.log                 one JSON line per build/edit/query (I/O counters)

Coordinates are PCM *frames*.  Audio is headless interleaved signed-16
little-endian PCM with 1..8 channels.  Index files are sequences of 4096-byte
pages; every data page has a CRC-32 in a checksum table inside the header, and
the header itself carries a self-check (CRC computed with its own slot zeroed).
Torn or corrupted pages are therefore detected instead of yielding stale peaks.

Semantics:
  * channels are handled independently and always reported in source order;
  * a silence segment contributes min=max=0 and sumsq=0 but still counts frames;
  * the final block of every level may be a partial block shorter than the
    level span; its frame count is fixed by geometry (frames - block*span);
  * empty ranges return min=max=rms=0 and consume no source bytes.
"""
import argparse
import array
import bisect
import hashlib
import json
import math
import os
import struct
import sys
import time
import zlib
from pathlib import Path

# ---------------------------------------------------------------- constants
PAGE = 4096
LEAF_FRAMES = 256                  # frames aggregated per leaf block
FANOUT = 32                        # child blocks per parent block
RECORD = 128                       # fixed padded per-block record size
RECORDS_PER_PAGE = PAGE // RECORD  # 32; records never straddle pages
MAGIC = b"WVIDX001"
KIND_SOURCE = 1
KIND_TIMELINE = 2
REGION_LEVEL = 1
REGION_FINGERPRINT = 2
# magic(8s) kind(H) channels frames nlevels edl_version header_crc
# checksum_start checksum_count nregions
HEADER_FMT = "<8sHIIIIIIII"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
REGION_FMT = "<IIII"               # kind, start_page, page_count, block_count
REGION_SIZE = struct.calcsize(REGION_FMT)
STAT_FMT = "<hhq"                  # per channel: min, max, sum-of-squares
STAT_BYTES = struct.calcsize(STAT_FMT)
FP_BYTES = 16                      # blake2b content fingerprint per leaf
EXIT_CRASH = 97


class IndexError_(Exception):
    """Corrupt or mismatched on-disk state; the CLI maps this to exit code 2."""


class IdentityMismatch(IndexError_):
    """Source file no longer matches the identity recorded at build time."""


# ---------------------------------------------------------------- small utils
def json_read(path):
    return json.loads(Path(path).read_text())


def atomic_write_json(dst, obj):
    dst = Path(dst)
    tmp = Path(str(dst) + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, allow_nan=False) + "\n")
    _fsync_dir(dst.parent)
    os.replace(tmp, dst)
    _fsync_dir(dst.parent)


def _fsync_dir(directory):
    try:
        os.fsync(os.open(directory, os.O_RDONLY))
    except OSError:
        pass


def append_stats(root, record):
    record = dict(record)
    record["ts"] = time.time()
    with open(Path(root) / "stats.log", "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _strict_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % name)
    return value


# ---------------------------------------------------------------- paged file
class PagedFile:
    """Random access over a page-oriented file; never reads it wholesale.

    `reads` counts distinct 4096-byte pages fetched, `read_bytes` the exact
    bytes pulled through pread.  All query I/O accounting comes from here.
    """

    def __init__(self, fd, path=None):
        self.fd = fd
        self.path = path
        self.reads = 0
        self.writes = 0
        self.read_bytes = 0
        self.write_bytes = 0
        self._touched = set()

    @classmethod
    def open(cls, path):
        return cls(os.open(path, os.O_RDONLY), Path(path))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_at(self, offset, size):
        data = os.pread(self.fd, size, offset)
        if len(data) != size:
            raise IndexError_("short read in %s" % self.path)
        self.read_bytes += size
        first, last = offset // PAGE, (offset + size - 1) // PAGE
        for pg in range(first, last + 1):
            if pg not in self._touched:
                self._touched.add(pg)
                self.reads += 1
        return data

    def write_at(self, offset, data):
        os.pwrite(self.fd, data, offset)
        self.write_bytes += len(data)
        first, last = offset // PAGE, (offset + len(data) - 1) // PAGE
        self.writes += last - first + 1

    def size(self):
        return os.fstat(self.fd).st_size


# ---------------------------------------------------------------- aggregate
class Agg:
    """Per-channel min/max/sumsq with an explicit frame count."""
    __slots__ = ("channels", "mins", "maxs", "sumsqs", "count")

    def __init__(self, channels, count=0):
        self.channels = channels
        self.mins = [0] * channels
        self.maxs = [0] * channels
        self.sumsqs = [0] * channels
        self.count = count

    def merge(self, other):
        if other.count == 0:
            return self
        if self.count == 0:
            self.mins = list(other.mins)
            self.maxs = list(other.maxs)
            self.sumsqs = list(other.sumsqs)
            self.count = other.count
            return self
        for ch in range(self.channels):
            if other.mins[ch] < self.mins[ch]:
                self.mins[ch] = other.mins[ch]
            if other.maxs[ch] > self.maxs[ch]:
                self.maxs[ch] = other.maxs[ch]
            self.sumsqs[ch] += other.sumsqs[ch]
        self.count += other.count
        return self

    def add_interleaved(self, samples, frame_count):
        """Aggregate `samples` = int values for frame_count interleaved frames."""
        n = self.channels
        for ch in range(n):
            mn = mx = None
            ss = 0
            base = ch
            for _ in range(frame_count):
                v = samples[base]
                if mn is None or v < mn:
                    mn = v
                if mx is None or v > mx:
                    mx = v
                ss += v * v
                base += n
            if frame_count:
                if self.count == 0:
                    self.mins[ch], self.maxs[ch] = mn, mx
                else:
                    self.mins[ch] = min(self.mins[ch], mn)
                    self.maxs[ch] = max(self.maxs[ch], mx)
                self.sumsqs[ch] += ss
        self.count += frame_count

    def to_record(self):
        out = bytearray(RECORD)
        for ch in range(self.channels):
            struct.pack_into(STAT_FMT, out, ch * STAT_BYTES,
                             self.mins[ch], self.maxs[ch], self.sumsqs[ch])
        return bytes(out)

    def load_record(self, buf):
        """Load one block record into *this* aggregate (count stays untouched)."""
        for ch in range(self.channels):
            mn, mx, ss = struct.unpack_from(STAT_FMT, buf, ch * STAT_BYTES)
            self.mins[ch], self.maxs[ch], self.sumsqs[ch] = mn, mx, ss
        return self

    def rms(self, ch):
        if self.count == 0:
            return 0.0
        return math.sqrt(self.sumsqs[ch] / self.count)


def level_span(level):
    return LEAF_FRAMES * (FANOUT ** level)


def blocks_for(frames, level):
    span = level_span(level)
    return (frames + span - 1) // span


def level_count(frames):
    if frames == 0:
        return 1
    level, count = 0, blocks_for(frames, 0)
    while count > 1:
        level += 1
        count = blocks_for(frames, level)
    return level + 1


# ---------------------------------------------------------------- index writer
class IndexBuilder:
    """Accumulates fixed-size block records per level, then publishes paged file."""

    def __init__(self, kind, channels, frames, edl_version=0,
                 fingerprints=None):
        self.kind = kind
        self.channels = channels
        self.frames = frames
        self.edl_version = edl_version
        self.nlevels = level_count(frames)
        self.records = [[] for _ in range(self.nlevels)]
        self.fingerprints = fingerprints  # list[16-byte digest] or None

    def add_leaf(self, agg):
        self.records[0].append(agg.to_record())

    def add_blank_leaf(self):
        self.records[0].append(bytes(RECORD))

    def build_parents(self):
        for level in range(1, self.nlevels):
            children = self.records[level - 1]
            out = self.records[level]
            for first in range(0, len(children), FANOUT):
                group = children[first:first + FANOUT]
                merged = Agg(self.channels)
                for buf in group:
                    part = Agg(self.channels, 1).load_record(buf)
                    merged.merge(part)
                out.append(merged.to_record())

    def _layout(self):
        has_fp = self.fingerprints is not None
        nregions = self.nlevels + (1 if has_fp else 0)
        header_need = HEADER_SIZE + nregions * REGION_SIZE
        header_pages = max(1, math.ceil(header_need / PAGE))
        while True:
            cursor = header_pages
            regions = []
            for level, recs in enumerate(self.records):
                pages = max(1, math.ceil(len(recs) * RECORD / PAGE))
                regions.append([REGION_LEVEL, cursor, pages, len(recs)])
                cursor += pages
            fp_len = sum(map(len, self.fingerprints)) if has_fp else 0
            if has_fp:
                fp_pages = max(1, math.ceil(fp_len / PAGE))
                regions.append([REGION_FINGERPRINT, cursor, fp_pages,
                                len(self.fingerprints)])
                cursor += fp_pages
            total_pages = cursor
            checksum_count = total_pages - header_pages
            need = header_need + checksum_count * 4
            new_hp = max(1, math.ceil(need / PAGE))
            if new_hp == header_pages:
                return header_pages, regions, total_pages, checksum_count
            header_pages = new_hp

    def write(self, dst):
        """Write to dst.tmp, fsync, then atomically rename; never half-publishes."""
        header_pages, regions, total_pages, checksum_count = self._layout()
        checksum_start = (HEADER_SIZE
                          + (self.nlevels + (
                              1 if self.fingerprints is not None else 0))
                          * REGION_SIZE)
        # magic kind channels frames nlevels edl_version checksum_start
        # checksum_count nregions header_crc
        header = struct.pack(
            HEADER_FMT, MAGIC, self.kind, self.channels, self.frames,
            self.nlevels, self.edl_version, checksum_start,
            checksum_count, len(regions), 0)
        for region in regions:
            header += struct.pack(REGION_FMT, *region)

        dst = Path(dst)
        tmp = Path(str(dst) + ".tmp")
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        pf = PagedFile(fd, tmp)
        try:
            level_regions = [r for r in regions if r[0] == REGION_LEVEL]
            for (_, start, pages, _), recs in zip(level_regions, self.records):
                blob = b"".join(recs)
                pf.write_at(start * PAGE,
                            blob + b"\x00" * (pages * PAGE - len(blob)))
            fp_regions = [r for r in regions if r[0] == REGION_FINGERPRINT]
            if fp_regions:
                region = fp_regions[0]
                blob = b"".join(self.fingerprints)
                pf.write_at(region[1] * PAGE,
                            blob + b"\x00" * (region[2] * PAGE - len(blob)))
            os.fsync(fd)
            crcs = [zlib.crc32(os.pread(fd, PAGE, pg * PAGE)) & 0xFFFFFFFF
                    for pg in range(header_pages, total_pages)]
            hdr = bytearray(header_pages * PAGE)
            hdr[:len(header)] = header
            hdr[checksum_start:checksum_start + len(crcs) * 4] = \
                struct.pack("<%dI" % len(crcs), *crcs)
            # zero the self-check slot, then CRC the full set of header pages
            hdr[HEADER_SIZE - 4:HEADER_SIZE] = b"\x00\x00\x00\x00"
            header_crc = zlib.crc32(bytes(hdr)) & 0xFFFFFFFF
            struct.pack_into("<I", hdr, HEADER_SIZE - 4, header_crc)
            pf.write_at(0, bytes(hdr))
            os.fsync(fd)
            pf.close()
            _fsync_dir(dst.parent)
            os.replace(tmp, dst)
            _fsync_dir(dst.parent)
        except BaseException:
            try:
                pf.close()
            except OSError:
                pass
            if tmp.exists():
                tmp.unlink()
            raise
        return {"pages": total_pages, "index_pages_written": pf.writes,
                "index_bytes_written": pf.write_bytes}


# ---------------------------------------------------------------- index reader
class IndexReader:
    """Validated paged reader; CRC failures raise IndexError_ on first touch."""

    def __init__(self, path, expected_kind):
        self.path = Path(path)
        self.pf = PagedFile.open(self.path)
        try:
            first = self.pf.read_at(0, PAGE)
            (magic, kind, self.channels, self.frames, self.nlevels,
             self.edl_version, checksum_start, checksum_count, nregions,
             header_crc) = struct.unpack_from(HEADER_FMT, first)
            if magic != MAGIC or kind != expected_kind:
                raise IndexError_("bad magic/kind in %s" % self.path)
            self.header_pages = math.ceil(
                (checksum_start + checksum_count * 4) / PAGE)
            if self.header_pages > 1:
                rest = self.pf.read_at(PAGE, (self.header_pages - 1) * PAGE)
                header_blob = first + rest
            else:
                header_blob = first
            slot = HEADER_SIZE - 4
            want = zlib.crc32(
                header_blob[:slot] + b"\x00\x00\x00\x00"
                + header_blob[HEADER_SIZE:]) & 0xFFFFFFFF
            if want != header_crc:
                raise IndexError_("header checksum failed in %s" % self.path)
            if len(header_blob) != self.header_pages * PAGE:
                raise IndexError_("short header in %s" % self.path)
            self._regions = {}
            self._level_regions = {}
            cursor = HEADER_SIZE
            for _ in range(nregions):
                rkind, start, pcount, bcount = struct.unpack_from(
                    REGION_FMT, header_blob, cursor)
                cursor += REGION_SIZE
                region = (start, pcount, bcount)
                self._regions.setdefault(rkind, []).append(region)
                if rkind == REGION_LEVEL:
                    self._level_regions[len(self._level_regions)] = region
            if len(self._level_regions) != self.nlevels:
                raise IndexError_("level table corrupt in %s" % self.path)
            self._checksum_start = checksum_start
            self._checksum_count = checksum_count
            self._header_blob = header_blob
            self._ok_pages = set()
        except Exception:
            self.pf.close()
            raise

    def close(self):
        self.pf.close()

    def _check_page(self, page):
        if page in self._ok_pages:
            return
        idx = page - self.header_pages
        if not (0 <= idx < self._checksum_count):
            raise IndexError_("page %d outside checksum table" % page)
        (stored,) = struct.unpack_from(
            "<I", self._header_blob, self._checksum_start + idx * 4)
        actual = zlib.crc32(self.pf.read_at(page * PAGE, PAGE)) & 0xFFFFFFFF
        if actual != stored:
            raise IndexError_("page %d checksum failed in %s"
                              % (page, self.path))
        self._ok_pages.add(page)

    def block_count(self, level):
        return self._level_regions[level][2]

    def block_record(self, level, index):
        start_page, _, count = self._level_regions[level]
        if not (0 <= index < count):
            raise IndexError_("block %d out of range at level %d"
                              % (index, level))
        offset = start_page * PAGE + index * RECORD
        page = offset // PAGE
        self._check_page(page)
        return self.pf.read_at(offset, RECORD)

    def fingerprint(self, leaf_index):
        regions = self._regions.get(REGION_FINGERPRINT)
        if not regions:
            raise IndexError_("index has no fingerprint region")
        start_page, _, count = regions[0]
        if not (0 <= leaf_index < count):
            raise IndexError_("fingerprint %d out of range" % leaf_index)
        offset = start_page * PAGE + leaf_index * FP_BYTES
        first_page = offset // PAGE
        self._check_page(first_page)
        end_page = (offset + FP_BYTES - 1) // PAGE
        if end_page != first_page:
            self._check_page(end_page)
        return self.pf.read_at(offset, FP_BYTES)


# ---------------------------------------------------------------- EDL pieces
class Timeline:
    """Materialised EDL: cumulative frame boundaries plus segment descriptors."""

    def __init__(self, segments, source_frames):
        self.segments = segments
        self.source_frames = source_frames
        self.boundaries = [0]
        total = 0
        for seg in segments:
            if "silence" in seg:
                total += seg["silence"]
            else:
                total += seg["end"] - seg["start"]
            self.boundaries.append(total)
        self.frames = total

    @staticmethod
    def validate(segments, source_frames):
        if not isinstance(segments, list):
            raise ValueError("segments must be a list")
        clean = []
        for seg in segments:
            if not isinstance(seg, dict) or not seg:
                raise ValueError("each segment must be a non-empty object")
            extra = set(seg) - {"start", "end", "silence"}
            if extra:
                raise ValueError("unknown segment keys: %s" % sorted(extra))
            if "silence" in seg:
                if set(seg) != {"silence"}:
                    raise ValueError("silence segment takes only 'silence'")
                length = _strict_int(seg["silence"], "silence")
                if length < 0:
                    raise ValueError("silence length must be >= 0")
                clean.append({"silence": length})
            else:
                if set(seg) != {"start", "end"}:
                    raise ValueError("source segment needs start and end")
                start = _strict_int(seg["start"], "start")
                end = _strict_int(seg["end"], "end")
                if not (0 <= start <= end <= source_frames):
                    raise ValueError(
                        "source range [%d,%d) outside [0,%d)"
                        % (start, end, source_frames))
                clean.append({"start": start, "end": end})
        return clean

    def pieces(self, start, end):
        """Yield (kind, src_or_-1, src_start, length) covering [start,end)."""
        if end <= start:
            return
        boundaries = self.boundaries
        seg_idx = bisect.bisect_right(boundaries, start) - 1
        pos = start
        while pos < end:
            seg = self.segments[seg_idx]
            seg_end = boundaries[seg_idx + 1]
            length = min(end, seg_end) - pos
            if "silence" in seg:
                yield ("Z", -1, -1, length)
            else:
                offset = pos - boundaries[seg_idx]
                yield ("S", 0, seg["start"] + offset, length)
            pos += length
            seg_idx += 1

    def leaf_fingerprint(self, leaf_start, leaf_end):
        h = hashlib.blake2b(digest_size=FP_BYTES)
        for kind, _, src_start, length in self.pieces(leaf_start, leaf_end):
            h.update(struct.pack(">cBqI", kind.encode(), 0, src_start, length))
        return h.digest()


# ---------------------------------------------------------------- raw reads
class SourceReader:
    """pread wrapper for headless interleaved PCM with identity enforcement."""

    def __init__(self, path, meta):
        self.path = Path(path)
        self.channels = meta["channels"]
        self.meta = meta
        st = self.path.stat()
        if st.st_size != meta["size"]:
            raise IdentityMismatch(
                "source size %d != recorded %d" % (st.st_size, meta["size"]))
        if st.st_mtime != meta["mtime"]:
            raise IdentityMismatch("source mtime changed since build")
        self.fd = os.open(self.path, os.O_RDONLY)
        self.raw_bytes = 0
        self.pages = set()

    def close(self):
        os.close(self.fd)

    def read_frames(self, start, count):
        """Return a flat list of signed int samples for frames [start,start+c)."""
        n = self.channels
        size = count * n * 2
        raw = os.pread(self.fd, size, start * n * 2)
        if len(raw) != size:
            raise IndexError_("short source read")
        self.raw_bytes += size
        first = (start * n * 2) // PAGE
        last = (start * n * 2 + size - 1) // PAGE
        self.pages.update(range(first, last + 1))
        vals = array.array("h")
        vals.frombytes(raw)
        if sys.byteorder != "little":
            vals.byteswap()
        return vals

    def materialize(self, timeline, start, end):
        """Aggregate timeline coordinates [start,end) from EDL pieces."""
        agg = Agg(self.channels)
        for kind, _, src_start, length in timeline.pieces(start, end):
            if kind == "Z":
                # silence: every sample is zero, which still counts as a frame
                if agg.count == 0:
                    agg.mins = [0] * self.channels
                    agg.maxs = [0] * self.channels
                else:
                    for ch in range(self.channels):
                        agg.mins[ch] = min(agg.mins[ch], 0)
                        agg.maxs[ch] = max(agg.maxs[ch], 0)
                agg.count += length
            else:
                samples = self.read_frames(src_start, length)
                agg.add_interleaved(samples, length)
        agg.count = end - start
        return agg


# ---------------------------------------------------------------- range walk
def range_stats(reader, start, end, partial):
    """Combine whole index blocks plus raw edge samples for [start,end).

    `partial(a, b)` materialises the partial leaf span [a,b) through either
    the raw source or the EDL.  Full aligned blocks at every level come from
    the index; only leaf blocks cut by the range edges touch raw samples.
    """
    if end <= start:
        return Agg(reader.channels, 0)
    frames = reader.frames
    if not (0 <= start <= end <= frames):
        raise ValueError("range [%d,%d) outside [0,%d)" % (start, end, frames))
    result = Agg(reader.channels)
    pos = start
    top = reader.nlevels - 1
    while pos < end:
        picked = None
        for level in range(top, 0, -1):
            span = level_span(level)
            index = pos // span
            block_begin = index * span
            block_end = min(block_begin + span, frames)
            # use the block when it is fully inside the remaining range;
            # alignment to the range start is not required
            if (block_begin >= pos and block_end <= end
                    and index < reader.block_count(level)):
                picked = (level, index, block_end - block_begin)
                break
        if picked is not None:
            level, index, actual_span = picked
            part = Agg(reader.channels, actual_span).load_record(
                reader.block_record(level, index))
            result.merge(part)
            pos += actual_span
            continue
        leaf_index = pos // LEAF_FRAMES
        leaf_begin = leaf_index * LEAF_FRAMES
        leaf_end = min(leaf_begin + LEAF_FRAMES, frames)
        if pos == leaf_begin and end >= leaf_end and leaf_end - leaf_begin \
                == LEAF_FRAMES and leaf_index < reader.block_count(0):
            part = Agg(reader.channels, LEAF_FRAMES).load_record(
                reader.block_record(0, leaf_index))
            result.merge(part)
            pos = leaf_end
        else:
            stop = min(end, leaf_end)
            result.merge(partial(leaf_index, pos, stop))
            pos = stop
    result.count = end - start
    return result


# ---------------------------------------------------------------- build
def build_source_index(source_path, meta, stats):
    channels = meta["channels"]
    frames = meta["frames"]
    builder = IndexBuilder(KIND_SOURCE, channels, frames)
    if frames == 0:
        builder.add_blank_leaf()
    fd = os.open(source_path, os.O_RDONLY)
    raw_bytes = 0
    try:
        for first in range(0, frames, LEAF_FRAMES):
            count = min(LEAF_FRAMES, frames - first)
            raw = os.pread(fd, count * channels * 2, first * channels * 2)
            if len(raw) != count * channels * 2:
                raise IndexError_("source shrank during build")
            raw_bytes += len(raw)
            vals = array.array("h")
            vals.frombytes(raw)
            if sys.byteorder != "little":
                vals.byteswap()
            agg = Agg(channels)
            agg.add_interleaved(vals, count)
            builder.add_leaf(agg)
    finally:
        os.close(fd)
    builder.build_parents()
    info = builder.write(Path(meta["root"]) / "source.idx")
    stats.update({"source_raw_bytes": raw_bytes, **info})


def build_timeline_index(root, meta, timeline, edl_version, old_reader,
                         source, stats, target=None):
    """Build derived index; reuse leaves whose content fingerprint is unchanged.

    The file is published atomically to `target` (default edl/index-V.idx).
    Passing a staging path lets the caller abort before any version is visible.
    """
    channels = meta["channels"]
    frames = timeline.frames
    leaves = blocks_for(frames, 0) if frames else 1
    builder = IndexBuilder(KIND_TIMELINE, channels, frames, edl_version,
                           fingerprints=[])
    reused = rebuilt = 0
    raw_before = source.raw_bytes if source else 0
    leaf_reused_flags = []
    old_fps = {}
    if old_reader is not None:
        for i in range(old_reader.block_count(0)):
            old_fps[old_reader.fingerprint(i)] = i
    for leaf in range(leaves):
        begin = leaf * LEAF_FRAMES
        end = min(begin + LEAF_FRAMES, frames) if frames else begin
        if frames == 0:
            builder.add_blank_leaf()
            builder.fingerprints.append(b"\x00" * FP_BYTES)
            leaf_reused_flags.append(False)
            rebuilt += 1
            continue
        fp = timeline.leaf_fingerprint(begin, end)
        builder.fingerprints.append(fp)
        if fp in old_fps:
            record = old_reader.block_record(0, old_fps[fp])
            builder.records[0].append(record)
            reused += 1
            leaf_reused_flags.append(True)
        else:
            agg = source.materialize(timeline, begin, end)
            builder.add_leaf(agg)
            rebuilt += 1
            leaf_reused_flags.append(False)
    builder.build_parents()
    # A parent block is provably invariant exactly when every block beneath
    # it was reused; propagate leaf reuse flags bottom-up.
    block_flags = [leaf_reused_flags]
    for level in range(1, builder.nlevels):
        child_flags = block_flags[level - 1]
        flags = []
        for index in range(len(builder.records[level])):
            first = index * FANOUT
            last = min((index + 1) * FANOUT, len(child_flags))
            flags.append(all(child_flags[first:last]))
        block_flags.append(flags)
    parent_reused = sum(sum(flags) for flags in block_flags[1:])
    parents = sum(len(r) for r in builder.records[1:])
    parent_rebuilt = parents - parent_reused
    info = builder.write(target if target is not None else
                         root / "edl" / ("index-%d.idx" % edl_version))
    stats.update({"leaves_total": leaves, "leaves_reused": reused,
                  "leaves_rebuilt": rebuilt, "parent_blocks": parents,
                  "parent_reused": parent_reused,
                  "parent_rebuilt": parent_rebuilt,
                  "derived_raw_bytes": (source.raw_bytes if source else 0)
                                       - raw_before, **info})


def cmd_build(root, source, channels):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "edl").mkdir(exist_ok=True)
    source = Path(source)
    if not (1 <= channels <= 8):
        raise ValueError("--channels must be between 1 and 8")
    data = source.read_bytes()
    frame_size = channels * 2
    if len(data) % frame_size != 0:
        raise ValueError("file size %d is not frame-aligned for %d channels"
                         % (len(data), channels))
    frames = len(data) // frame_size
    digest = hashlib.sha256(data).hexdigest()
    st = source.stat()
    meta = {"source": str(source.resolve()), "channels": channels,
            "frames": frames, "size": st.st_size, "mtime": st.st_mtime,
            "digest": digest, "root": str(root.resolve())}
    stats = {"op": "build"}
    atomic_write_json(root / "meta.json", meta)
    t0 = time.time()
    build_source_index(source, meta, stats)
    timeline = Timeline(
        Timeline.validate([{"start": 0, "end": frames}], frames), frames)
    atomic_write_json(root / "edl" / "edl-1.json",
                      {"version": 1, "segments": timeline.segments})
    # version 1 timeline is exactly the source: reuse the source leaf records
    # verbatim instead of re-reading raw samples.
    src_reader = IndexReader(root / "source.idx", KIND_SOURCE)
    try:
        builder = IndexBuilder(KIND_TIMELINE, channels, frames, 1,
                               fingerprints=[])
        if frames == 0:
            builder.add_blank_leaf()
            builder.fingerprints.append(b"\x00" * FP_BYTES)
        for i in range(src_reader.block_count(0) if frames else 0):
            rec = src_reader.block_record(0, i)
            builder.records[0].append(rec)
            h = hashlib.blake2b(digest_size=FP_BYTES)
            begin = i * LEAF_FRAMES
            end = min(begin + LEAF_FRAMES, frames)
            h.update(struct.pack(">cBqI", b"S", 0, begin, end - begin))
            builder.fingerprints.append(h.digest())
        if frames == 0:
            builder.add_blank_leaf()
        builder.build_parents()
        info = builder.write(root / "edl" / "index-1.idx")
        stats["v1_pages"] = info["pages"]
        stats["v1_reused_leaves"] = src_reader.block_count(0)
    finally:
        src_reader.close()
    atomic_write_json(root / "current.txt", {"version": 1})
    stats["elapsed"] = time.time() - t0
    append_stats(root, stats)
    return {"version": 1, "frames": frames, "digest": digest}


# ---------------------------------------------------------------- edit
def cmd_edit(root, edl_path, crash):
    root = Path(root)
    meta = json_read(root / "meta.json")
    current = json_read(root / "current.txt")["version"]
    segments = Timeline.validate(json_read(edl_path)["segments"],
                                 meta["frames"])
    new_version = current + 1
    timeline = Timeline(segments, meta["frames"])
    atomic_write_json(root / "edl" / ("edl-%d.json.tmp" % new_version),
                      {"version": new_version, "segments": segments})
    stats = {"op": "edit", "version": new_version, "from_version": current,
             "segments": len(segments), "timeline_frames": timeline.frames}
    t0 = time.time()
    source = SourceReader(meta["source"], meta)
    old_reader = None
    stage = root / "edl" / ("index-%d.idx.stage" % new_version)
    if stage.exists():
        stage.unlink()
    try:
        old_index = root / "edl" / ("index-%d.idx" % current)
        if old_index.exists():
            try:
                old_reader = IndexReader(old_index, KIND_TIMELINE)
            except (IndexError_, FileNotFoundError):
                old_reader = None  # corrupt/missing old index: full rebuild
        build_timeline_index(root, meta, timeline, new_version, old_reader,
                             source, stats, target=stage)
    finally:
        if old_reader is not None:
            old_reader.close()
        source.close()
    stats["raw_bytes"] = source.raw_bytes
    stats["elapsed"] = time.time() - t0
    if crash:
        # nothing for the new version has been published under its real name
        for tmp in (root / "edl").glob("*.tmp"):
            tmp.unlink()
        if stage.exists():
            stage.unlink()
        stats["crashed"] = True
        append_stats(root, stats)
        sys.stderr.write("crash-before-publish: version %d not published\n"
                         % new_version)
        sys.exit(EXIT_CRASH)
    # publish point: both artifacts move to their real names atomically
    os.replace(stage, root / "edl" / ("index-%d.idx" % new_version))
    os.replace(root / "edl" / ("edl-%d.json.tmp" % new_version),
               root / "edl" / ("edl-%d.json" % new_version))
    _fsync_dir(root / "edl")
    atomic_write_json(root / "current.txt", {"version": new_version})
    append_stats(root, stats)
    return {"version": new_version, "timeline_frames": timeline.frames,
            "leaves_reused": stats["leaves_reused"],
            "leaves_rebuilt": stats["leaves_rebuilt"]}


# ---------------------------------------------------------------- query
def _load_version(root, version):
    state = json_read(root / "edl" / ("edl-%d.json" % version))
    if state["version"] != version:
        raise IndexError_("EDL version mismatch in edl-%d.json" % version)
    return state


def cmd_query(root, ranges_path, wanted_version, verify_digest=False):
    root = Path(root)
    meta = json_read(root / "meta.json")
    current = json_read(root / "current.txt")["version"]
    version = wanted_version if wanted_version is not None else current
    if not (1 <= version <= current):
        raise ValueError("unknown version %d (latest %d)" % (version, current))
    state = _load_version(root, version)
    timeline = Timeline(state["segments"], meta["frames"])
    stats = {"op": "query", "version": version, "recovered": False,
             "digest_verified": False}
    t0 = time.time()
    if verify_digest:
        # explicit full re-hash; normal queries rely on size+mtime identity
        h = hashlib.sha256()
        with open(meta["source"], "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta["digest"]:
            raise IdentityMismatch("source digest differs from build record")
        stats["digest_verified"] = True

    source_idx_path = root / "source.idx"
    derived_path = root / "edl" / ("index-%d.idx" % version)
    queries = json_read(ranges_path)["ranges"]

    def rebuild_derived():
        state_ = _load_version(root, version)
        tl_ = Timeline(state_["segments"], meta["frames"])
        src_ = SourceReader(meta["source"], meta)
        try:
            rec = {"op": "recover-derived-index", "version": version}
            build_timeline_index(root, meta, tl_, version, None, src_, rec)
        finally:
            src_.close()
        append_stats(root, rec)
        stats["recovered"] = True

    try:
        source_idx = IndexReader(source_idx_path, KIND_SOURCE)
    except (IndexError_, FileNotFoundError):
        rebuild = {"op": "recover-source-index"}
        build_source_index(meta["source"], meta, rebuild)
        append_stats(root, rebuild)
        stats["recovered"] = True
        source_idx = IndexReader(source_idx_path, KIND_SOURCE)

    def execute():
        """One full read pass; raises IndexError_ on any damaged data page."""
        try:
            reader = IndexReader(derived_path, KIND_TIMELINE)
        except (IndexError_, FileNotFoundError):
            rebuild_derived()
            reader = IndexReader(derived_path, KIND_TIMELINE)
        try:
            if (reader.edl_version != version
                    or reader.frames != timeline.frames
                    or reader.channels != meta["channels"]):
                raise IdentityMismatch(
                    "derived index does not match EDL version %d" % version)
            source = SourceReader(meta["source"], meta)
            out = []
            try:
                for q in queries:
                    qid = q["id"]
                    start = _strict_int(q["start"], "start")
                    end = _strict_int(q["end"], "end")
                    if not (0 <= start <= end <= timeline.frames):
                        raise ValueError("range %r outside timeline" % (qid,))
                    agg = range_stats(
                        reader, start, end,
                        lambda _li, a, b: source.materialize(timeline, a, b))
                    out.append({"id": qid, "channels": [
                        {"min": agg.mins[ch], "max": agg.maxs[ch],
                         "rms": agg.rms(ch)}
                        for ch in range(meta["channels"])]})
                return out, source.raw_bytes, reader.pf.reads
            finally:
                source.close()
        finally:
            reader.close()

    try:
        if (source_idx.channels != meta["channels"]
                or source_idx.frames != meta["frames"]):
            raise IdentityMismatch("source index does not match meta.json")
        # touch the single root block so a corrupted source index data page is
        # detected here (and rebuilt) instead of lying dormant on disk
        try:
            source_idx.block_record(source_idx.nlevels - 1, 0)
        except IndexError_:
            rebuild = {"op": "recover-source-index"}
            build_source_index(meta["source"], meta, rebuild)
            append_stats(root, rebuild)
            stats["recovered"] = True
            source_idx.close()
            source_idx = IndexReader(source_idx_path, KIND_SOURCE)
        try:
            output, raw_bytes, derived_pages = execute()
        except IndexError_:
            # lazy page CRC failed mid-query: rebuild from source/EDL, retry
            rebuild_derived()
            output, raw_bytes, derived_pages = execute()
        stats["raw_bytes"] = raw_bytes
        stats["index_pages"] = source_idx.pf.reads + derived_pages
        stats["ranges"] = len(output)
    finally:
        source_idx.close()
    stats["elapsed"] = time.time() - t0
    append_stats(root, stats)
    return {"version": version, "ranges": output,
            "raw_bytes": stats["raw_bytes"],
            "index_pages": stats["index_pages"]}


# ---------------------------------------------------------------- cli
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="op", required=True)
    b = sub.add_parser("build")
    b.add_argument("source", type=Path)
    b.add_argument("--channels", type=int, required=True)
    e = sub.add_parser("edit")
    e.add_argument("edl", type=Path)
    e.add_argument("--crash-before-publish", action="store_true")
    q = sub.add_parser("query")
    q.add_argument("ranges", type=Path)
    q.add_argument("--version", type=int)
    q.add_argument("--verify-digest", action="store_true",
                   help="re-hash the full source and reject digest mismatch")
    args = parser.parse_args()
    try:
        if args.op == "build":
            out = cmd_build(args.root, args.source, args.channels)
        elif args.op == "edit":
            out = cmd_edit(args.root, args.edl, args.crash_before_publish)
        else:
            out = cmd_query(args.root, args.ranges, args.version,
                            args.verify_digest)
        print(json.dumps(out, sort_keys=True))
    except (ValueError, OSError, IndexError_) as error:
        parser.exit(2, "%s: %s\n" % (type(error).__name__, error))


if __name__ == "__main__":
    main()
