"""Streams and per-stream chunk index of an AVI file.

Both the classic ``idx1`` index and the OpenDML super/standard indexes
(``indx`` + ``ix##``) are understood.  Every index entry is turned into the
absolute file position of the chunk data, which is what a lossless cutter
needs.

Classic ``idx1`` offsets are documented as relative to the ``movi`` fourcc,
but some recorders write them relative to the start of the file, and a few
write data offsets instead of chunk-header offsets.  Instead of trusting one
convention, candidate bases are validated against the actual bytes in the
file and the best fit is used.
"""
from fractions import Fraction

from .riff import AviError, find, find_all, flatten, segments

# idx1 flags; AVIIF_KEYFRAME marks a frame that can be decoded on its own.
AVIIF_KEYFRAME = 0x10

# AVI index types (AVISTDINDEX / AVISUPERINDEX bIndexType).  Bit 7 marks an
# index that describes non-data chunks and is masked off before comparing.
AVI_INDEX_OF_INDEXES = 0
AVI_INDEX_OF_CHUNKS = 1
AVI_INDEX_OF_SUB_2FIELD = 3

# Bit in a standard-index entry's dwSize that marks a key frame.
AVIIF_INDEX_KEYFRAME = 0x80000000


def _u16(data, pos):
    return int.from_bytes(data[pos:pos + 2], "little")


def _u32(data, pos):
    return int.from_bytes(data[pos:pos + 4], "little")


def _u64(data, pos):
    return int.from_bytes(data[pos:pos + 8], "little")


def _fourcc(raw):
    return raw.decode("latin-1")


def _stream_no(ckid):
    """Stream number encoded in a chunk id such as ``00dc`` or ``01wb``."""
    if len(ckid) < 2:
        return None
    try:
        return int(ckid[:2], 10)
    except ValueError:
        try:
            return int(ckid[:2], 16)
        except ValueError:
            return None


def streams(data, hdrl):
    """Per-stream information; the rate comes from each stream header."""
    out = []
    if hdrl is None:
        return out
    # Only used when a stream header carries no rate of its own.  avih's
    # dwMicroSecPerFrame is rounded and drifts on 30000/1001 material, so it
    # is never preferred over strh.
    avih = find(hdrl.children, "avih")
    usec = _u32(data, avih.offset + 8) if avih is not None else 0
    for strl in find_all(hdrl.children, "LIST", "strl"):
        strh = find(strl.children, "strh")
        if strh is None or strh.offset + 8 + 36 > len(data):
            continue
        base = strh.offset + 8
        kind = data[base:base + 4].decode("latin-1")
        handler = data[base + 4:base + 8].decode("latin-1")
        scale = _u32(data, base + 20)
        rate = _u32(data, base + 24)
        length = _u32(data, base + 32)
        # strh.dwRate / strh.dwScale is the exact frame rate, e.g.
        # 30000/1001, which avih's rounded microsecond field cannot express.
        if scale and rate:
            fps = Fraction(rate, scale)
        elif kind == "vids" and usec:
            fps = Fraction(1000000, usec)
        else:
            fps = Fraction(0)
        out.append({"type": kind, "handler": handler,
                    "rate": str(fps), "length": length})
    return out


# ---------------------------------------------------------------------------
# Locating chunk data from an index entry
# ---------------------------------------------------------------------------

def _header_quality(data, pos, ckid, size):
    """How well *pos* looks like the header of a chunk.

    2 = chunk id and size both match, 1 = id matches, 0 = no match.
    """
    if pos < 0 or pos + 8 > len(data):
        return 0
    if data[pos:pos + 4] != ckid:
        return 0
    if _u32(data, pos + 4) == size:
        return 2
    return 1


def _header_positions(mode, offset):
    base, delta = mode
    return base + offset + delta


SAMPLE = 64


def _sample(records):
    """A few records spread over the index, enough to pick a convention."""
    if len(records) <= SAMPLE:
        return records
    step = len(records) // SAMPLE
    return records[::step][:SAMPLE]


def _sample_indices(count):
    """Evenly spaced entry positions in an index of *count* records."""
    if count <= SAMPLE:
        return range(count)
    step = max(count // SAMPLE, 1)
    return range(0, count, step)


def _pick_mode(data, records, candidates):
    """Choose the (base, delta) pair that best matches the real chunk bytes.

    *records* holds ``(ckid, size, offset)`` tuples.  Only a sample is
    checked: 1 GB files have millions of entries.
    """
    best = candidates[0]
    best_score = -1
    sample = _sample(records)
    for mode in candidates:
        score = 0
        for ckid, size, offset in sample:
            score += _header_quality(data, _header_positions(mode, offset),
                                     ckid, size)
        if score > best_score:
            best, best_score = mode, score
    return best


def _header_for(data, ckid, size, offset, mode, candidates):
    pos = _header_positions(mode, offset)
    if _header_quality(data, pos, ckid, size):
        return pos
    for cand in candidates:
        pos = _header_positions(cand, offset)
        if _header_quality(data, pos, ckid, size):
            return pos
    return _header_positions(mode, offset)


# ---------------------------------------------------------------------------
# Classic idx1 index
# ---------------------------------------------------------------------------

def _idx1_count(data, chunk):
    pos = chunk.offset + 8
    return max((min(pos + chunk.size, len(data)) - pos) // 16, 0)


def _idx1_record(data, pos, i):
    e = pos + 16 * i
    return (_fourcc(data[e:e + 4]), _u32(data, e + 4),
            _u32(data, e + 8), _u32(data, e + 12))


def _idx1_candidates(seg, movi):
    bases = []
    if movi is not None:
        bases.append(movi.offset + 8)     # 'movi' fourcc: documented base
        bases.append(movi.offset + 12)    # first chunk header
    bases.append(seg.offset)              # relative to this RIFF segment
    bases.append(0)                       # offsets relative to file start
    cands = []
    for base in bases:
        cands.append((base, 0))           # offset points at the chunk header
        cands.append((base, -8))          # offset points at the chunk data
    return cands


def _iter_idx1(data, riffs):
    """Yield ``(stream number, entry)`` from every idx1 in the file.

    A generator so that multi-gigabyte recordings never need one Python
    object per frame.
    """
    for seg in riffs:
        movi = find(seg.children, "LIST", "movi")
        candidates = _idx1_candidates(seg, movi)
        for idx1 in find_all(seg.children, "idx1"):
            pos = idx1.offset + 8
            count = _idx1_count(data, idx1)

            def useful(i, pos=pos):
                ckid = _fourcc(data[pos + 16 * i:pos + 16 * i + 4])
                # Palette-change chunks ('##pc') are indexed too but are not
                # frames, so they must not inflate the chunk count.
                return _stream_no(ckid) is not None and ckid[2:4] != "pc"

            sample = [i for i in _sample_indices(count) if useful(i)]
            if not sample:
                continue
            mode = _pick_mode(
                data,
                [(_fourcc(data[pos + 16 * i:pos + 16 * i + 4]),
                  _u32(data, pos + 16 * i + 12), _u32(data, pos + 16 * i + 8))
                 for i in sample],
                candidates)
            for i in range(count):
                if not useful(i):
                    continue
                ckid, flags, offset, size = _idx1_record(data, pos, i)
                hdr = _header_for(data, ckid.encode("latin-1"), size, offset,
                                  mode, candidates)
                yield _stream_no(ckid), {
                    "offset": hdr + 8,
                    "size": size,
                    "key": bool(flags & AVIIF_KEYFRAME),
                }


# ---------------------------------------------------------------------------
# OpenDML indexes
# ---------------------------------------------------------------------------

def _movi_bases(riffs):
    bases = []
    for seg in riffs:
        movi = find(seg.children, "LIST", "movi")
        if movi is not None:
            bases.extend((movi.offset + 8, movi.offset + 12))
    return bases


def _standard_entries(data, pos, size, extra_bases):
    """Yield the index entries of an AVISTDINDEX body at *pos*."""
    stop = min(pos + size, len(data))
    if pos + 24 > stop:
        return
    longs = _u16(data, pos)
    itype = data[pos + 3] & 0x7f
    count = _u32(data, pos + 4)
    ckid = _fourcc(data[pos + 8:pos + 12])
    if itype not in (AVI_INDEX_OF_CHUNKS, AVI_INDEX_OF_SUB_2FIELD):
        return
    base_offset = _u64(data, pos + 12)
    step = max(longs * 4, 8)
    count = min(count, max((stop - pos - 24) // step, 0))
    if not count:
        return
    ckid_raw = ckid.encode("latin-1")

    # dwOffset is normally relative to qwBaseOffset and points at the chunk
    # data; header-relative and absolute variants exist as well.
    candidates = [(base_offset, -8), (base_offset, 0)]
    for base in extra_bases:
        candidates.append((base, -8))
        candidates.append((base, 0))
    candidates += [(0, -8), (0, 0)]

    def record(i):
        e = pos + 24 + i * step
        dw_size = _u32(data, e + 4)
        return (_u32(data, e), dw_size & 0x7fffffff,
                bool(dw_size & AVIIF_INDEX_KEYFRAME))

    mode = _pick_mode(data, [(ckid_raw, s, o)
                             for o, s, _ in map(record, _sample_indices(count))],
                      candidates)

    for i in range(count):
        offset, entry_size, key = record(i)
        hdr = _header_for(data, ckid_raw, entry_size, offset, mode, candidates)
        yield {"offset": hdr + 8, "size": entry_size, "key": key}


def _super_offsets(body, index, step, count):
    """Candidate ix## chunk positions for every super-index entry.

    The documented entry is ``dwDuration, dwSize, dwOffset, dwReserved`` and
    dwOffset is absolute.  Some recorders instead store the 64-bit offset
    first, so both readings are returned and validated against the file.
    """
    out = []
    for i in range(count):
        e = index + i * step
        if e + 16 > len(body):
            break
        cands = [_u32(body, e + 8),           # dwDuration, dwSize, dwOffset
                 _u64(body, e + 8),           # dwDuration, dwSize, dwOffset (64)
                 _u64(body, e),               # dwOffset first (recorders)
                 _u32(body, e)]
        out.append([o for o in cands if o])
    return out


def _resolve_ix_chunk(data, offsets, candidates, stream_no):
    """Position of the ix## chunk referenced by a super-index entry."""
    wanted = b"ix%02d" % stream_no
    for offset in offsets:
        for base, delta in candidates:
            pos = base + offset + delta
            if 0 <= pos <= len(data) - 8 and data[pos:pos + 4] == wanted:
                return pos
        for pos in (offset, offset - 8, offset + 8):
            if 0 <= pos <= len(data) - 8 and data[pos:pos + 4] == wanted:
                return pos
    return None


def _scan_ix_chunks(data, riffs, stream_no):
    """Fallback: find ix## chunks by walking the segment chunk headers."""
    wanted = b"%02d" % stream_no
    found = []
    for seg in riffs:
        regions = [(seg.offset + 12, seg.offset + 8 + seg.size)]
        movi = find(seg.children, "LIST", "movi")
        if movi is not None:
            regions.append((movi.offset + 12, movi.offset + 8 + movi.size))
        for start, end in regions:
            pos = start
            stop = min(end, len(data))
            while pos + 8 <= stop:
                cid = data[pos:pos + 4]
                size = _u32(data, pos + 4)
                if cid[:2] == b"ix" and cid[2:4] == wanted:
                    found.append(pos)
                pos += 8 + size + (size & 1)
    return sorted(set(found))


def _segment_bases(riffs):
    """Bases for indexes whose offsets count from a RIFF segment start."""
    bases = [0]
    for seg in riffs:
        bases.append(seg.offset)
    return bases


def _iter_odml(data, hdrl, riffs):
    """Yield ``(stream number, entry)`` from the OpenDML indexes."""
    if hdrl is None:
        return

    movi_bases = _movi_bases(riffs)
    std_extra = _segment_bases(riffs) + movi_bases
    # Super-index entries hold absolute file offsets in normally written
    # files; segment/movi-relative variants are tolerated.  Standalone indexes
    # (type CHUNKS directly in strl) may use their own absolute base.
    super_candidates = [(0, 0), (0, -8)]
    for base in _segment_bases(riffs) + movi_bases:
        super_candidates.append((base, 0))
        super_candidates.append((base, -8))

    for strl in find_all(hdrl.children, "LIST", "strl"):
        for indx in find_all(strl.children, "indx"):
            body = data[indx.offset + 8:indx.offset + 8 + indx.size]
            if len(body) < 24:
                continue
            longs = _u16(body, 0)
            itype = body[3] & 0x7f
            count = _u32(body, 4)
            ckid = _fourcc(body[8:12])
            no = _stream_no(ckid)
            if no is None:
                continue

            if itype == AVI_INDEX_OF_INDEXES:
                step = max(longs * 4, 16)
                refs = _super_offsets(body, 24, step, count)
                if not refs:
                    continue

                ix_chunks = []
                for offsets in refs:
                    pos = _resolve_ix_chunk(data, offsets, super_candidates, no)
                    if pos is not None:
                        ix_chunks.append(pos)
                if not ix_chunks:
                    ix_chunks = _scan_ix_chunks(data, riffs, no)

                for pos in ix_chunks:
                    size = _u32(data, pos + 4)
                    for e in _standard_entries(data, pos + 8, size, std_extra):
                        yield no, e

            elif itype in (AVI_INDEX_OF_CHUNKS, AVI_INDEX_OF_SUB_2FIELD):
                for e in _standard_entries(data, indx.offset + 8, indx.size,
                                           std_extra):
                    yield no, e


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def entry_map(data, riffs):
    """Classic idx1 entries keyed by stream number."""
    out = {}
    for no, e in _iter_idx1(data, riffs):
        out.setdefault(no, []).append(e)
    return out


def entries(data, top):
    """Classic idx1 entries keyed by stream number.

    *top* is a chunk list, normally ``walk(data, 0, len(data))`` or the
    segments returned by :func:`aviscan.riff.segments`.
    """
    seg = top if isinstance(top, list) else [top]
    return entry_map(data, flatten(seg))


def _summarize(seq):
    """Count chunks and key frames per stream without keeping every entry."""
    counts = {}
    keys = {}
    first = {}
    for no, entry in seq:
        counts[no] = counts.get(no, 0) + 1
        if entry["key"]:
            keys[no] = keys.get(no, 0) + 1
        bucket = first.setdefault(no, [])
        if len(bucket) < 3:
            bucket.append(entry)
    return counts, keys, first


def scan(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise AviError("not an AVI file")
    riffs = flatten(segments(data))
    if not riffs:
        raise AviError("not an AVI file")
    hdrl = find(riffs[0].children, "LIST", "hdrl")
    if hdrl is None:
        raise AviError("AVI file has no hdrl list")

    info = streams(data, hdrl)
    # OpenDML indexes take precedence; idx1 is the fallback.
    counts, keys, first = _summarize(_iter_odml(data, hdrl, riffs))
    legacy = None

    for n, s in enumerate(info):
        if n not in counts:
            if legacy is None:
                legacy = _summarize(_iter_idx1(data, riffs))
            lcounts, lkeys, lfirst = legacy
            if n in lcounts:
                counts[n] = lcounts[n]
                keys[n] = lkeys.get(n, 0)
                first[n] = lfirst[n]
        s["chunks"] = counts.get(n, 0)
        s["keyframes"] = keys.get(n, 0)
        s["first"] = first.get(n, [])
        if s["type"] == "vids":
            fps = Fraction(s["rate"])
            s["duration"] = (str(Fraction(s["chunks"], 1) / fps) if fps else "0")
    return info
