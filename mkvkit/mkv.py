"""Matroska / WebM: tracks and cluster blocks (RFC 9559)."""
import bz2
import struct
import zlib
from fractions import Fraction

from .ebml import EbmlError, element, uint, vint

SEGMENT, INFO, TRACKS, CLUSTER = 0x18538067, 0x1549A966, 0x1654AE6B, 0x1F43B675
TRACK_TYPES = {1: "video", 2: "audio", 3: "complex", 0x10: "logo", 0x11: "subtitle", 0x12: "buttons", 0x20: "control"}

# Children of a Cluster (RFC 9559 section 5.1.3) plus the global Void and
# CRC-32 elements.  An element outside this set ends a Cluster that was
# written with an unknown size (RFC 8794 section 4.3).
CLUSTER_CHILD = {0xE7, 0x5854, 0xA7, 0xAB, 0xA3, 0xA0, 0xAF, 0xEC, 0xBF}

# Children of a BlockGroup (RFC 9559 section 5.1.3.2) plus Void and CRC-32.
BLOCKGROUP_CHILD = {0x75A1, 0x9B, 0xFA, 0xFB, 0xFD, 0xA1, 0xA2, 0xA4,
                    0x75A2, 0x8E, 0xEC, 0xBF}


def children(data, start, end):
    pos = start
    while pos < end:
        try:
            eid, body, size = element(data, pos)
        except EbmlError:
            return
        if size is None:
            if eid == 0xA0:
                # An unknown-sized BlockGroup inside a known master element.
                nxt = group_end(data, body, end)
            else:
                return
        else:
            nxt = body + size
        if nxt > end or nxt < body:
            return
        yield eid, body, nxt
        pos = nxt


def read_info(data, s, e):
    scale, duration = 1000000, None
    for eid, b, f in children(data, s, e):
        if eid == 0x2AD7B1:
            scale = uint(data[b:f])
        elif eid == 0x4489:
            raw = data[b:f]
            if len(raw) == 4:
                duration = struct.unpack(">f", raw)[0]
            elif len(raw) == 8:
                duration = struct.unpack(">d", raw)[0]
            elif len(raw) == 0:
                duration = 0.0
            else:
                raise EbmlError("invalid Duration size")
    return scale, duration


def decode_text(raw):
    return raw.decode("ascii", "replace")


def read_encodings(data, s, e):
    """ContentEncodings of one track, in application order."""
    out = []
    for eid, b, f in children(data, s, e):
        if eid != 0x6240:
            continue
        # RFC 9559 section 5.1.4.1.2: order 0, scope 1 (frame data) and
        # type 0 (compression) are the defaults.
        enc = {"order": 0, "scope": 1, "type": 0, "algo": 0, "settings": b""}
        for cid, cb, cf in children(data, b, f):
            if cid == 0x5031:
                enc["order"] = uint(data[cb:cf])
            elif cid == 0x5032:
                enc["scope"] = uint(data[cb:cf])
            elif cid == 0x5033:
                enc["type"] = uint(data[cb:cf])
            elif cid == 0x5034:
                for did, db, df in children(data, cb, cf):
                    if did == 0x4254:
                        enc["algo"] = uint(data[db:df])
                    elif did == 0x4255:
                        enc["settings"] = data[db:df]
        out.append(enc)
    out.sort(key=lambda item: item["order"])
    return out


def read_tracks(data, s, e):
    out = {}
    for eid, b, f in children(data, s, e):
        if eid != 0xAE:
            continue
        t = {"number": None, "type": None, "codec": None, "enabled": True, "default": True,
             "language": "eng", "encodings": []}
        language = bcp47 = None
        for cid, cb, cf in children(data, b, f):
            if cid == 0xD7:
                t["number"] = uint(data[cb:cf])
            elif cid == 0x83:
                t["type"] = TRACK_TYPES.get(uint(data[cb:cf]), "unknown")
            elif cid == 0x86:
                t["codec"] = decode_text(data[cb:cf])
            elif cid == 0xB9:
                t["enabled"] = bool(uint(data[cb:cf]))
            elif cid == 0x88:
                t["default"] = bool(uint(data[cb:cf]))
            elif cid == 0x22B59C:
                language = decode_text(data[cb:cf])
            elif cid == 0x22B59D:
                bcp47 = decode_text(data[cb:cf])
            elif cid == 0x6D80:
                t["encodings"] = read_encodings(data, cb, cf)
        if bcp47:
            t["language"] = bcp47
        elif language is not None:
            t["language"] = language
        if t["number"] is not None:
            out[t["number"]] = t
    return out


def lace_sizes(data, p, end, flags):
    """Return (frame sizes, start of frame data) for a block body at p."""
    lacing = (flags >> 1) & 3
    if lacing == 0:
        if p > end:
            raise EbmlError("truncated block")
        return [end - p], p
    if p >= end:
        raise EbmlError("truncated lacing")
    count = data[p] + 1
    p += 1
    if count == 1:
        # Only the lace count byte is present when there is a single frame.
        return [end - p], p
    if lacing == 1:
        # Xiph lacing: each frame size is a sum of 255s and a final byte.
        sizes = []
        for _ in range(count - 1):
            size = 0
            while True:
                if p >= end:
                    raise EbmlError("truncated lacing")
                byte = data[p]
                p += 1
                size += byte
                if byte != 255:
                    break
            sizes.append(size)
        sizes.append(end - p - sum(sizes))
    elif lacing == 2:
        # Fixed-size lacing: every frame has the same size.  Keep the sizes
        # summing to the payload if a writer rounded the split.
        size, extra = divmod(end - p, count)
        sizes = [size] * count
        sizes[-1] += extra
    else:
        # EBML lacing: the first size is unsigned, each following size is
        # the signed difference to the previous one, biased so that it can
        # be coded as an unsigned variable-length integer (RFC 9559 5.1.3).
        first, n, _ = vint(data, p)
        p += n
        if p > end:
            raise EbmlError("truncated lacing")
        sizes = [first]
        for _ in range(count - 2):
            raw, n, _ = vint(data, p)
            p += n
            if p > end:
                raise EbmlError("truncated lacing")
            bias = (1 << (7 * n - 1)) - 1
            sizes.append(sizes[-1] + raw - bias)
            if sizes[-1] < 0:
                raise EbmlError("negative lace size")
        sizes.append(end - p - sum(sizes))
    if any(size < 0 for size in sizes):
        raise EbmlError("negative lace size")
    return sizes, p


def block(data, b, f):
    track, n, _ = vint(data, b)
    if b + n + 3 > f:
        raise EbmlError("truncated block")
    (rel,) = struct.unpack_from(">h", data, b + n)
    flags = data[b + n + 2]
    sizes, start = lace_sizes(data, b + n + 3, f, flags)
    return track, rel, flags, sizes, start


def decode_frame(frame, encodings):
    """Undo the track's content encodings on one frame (RFC 9559 5.1.4).

    ``encodings`` is ordered by ContentEncodingOrder and applied in reverse:
    the encoder applies order 0 first, so the decoder undoes the highest
    order first.
    """
    for enc in reversed(encodings):
        # Scope bit 0 covers the frame data (lacing is never encoded);
        # Scope bit 1 covers the track's CodecPrivate.
        if not enc["scope"] & 0x1 or enc["type"] != 0:
            continue
        algo = enc["algo"]
        try:
            if algo == 0:
                # zlib; some writers use a raw deflate stream instead.
                try:
                    frame = zlib.decompress(frame)
                except zlib.error:
                    frame = zlib.decompress(frame, -zlib.MAX_WBITS)
            elif algo == 1:
                frame = bz2.decompress(frame)
            elif algo == 3:
                # Header stripping: the stripped prefix is in the settings.
                frame = enc["settings"] + frame
            # Other algorithms (LZO, encryption) have no stdlib decoder.
        except (zlib.error, OSError, ValueError):
            pass
    return frame


def frame_bytes(data, start, sizes, encodings):
    if not encodings:
        return sum(sizes)
    total = 0
    pos = start
    for size in sizes:
        frame = data[pos:pos + size]
        pos += size
        total += len(decode_frame(frame, encodings))
    return total


def group_end(data, start, limit):
    """End of an unknown-sized BlockGroup (RFC 8794 section 4.3)."""
    pos = start
    while pos < limit:
        try:
            eid, body, size = element(data, pos)
        except EbmlError:
            break
        if eid not in BLOCKGROUP_CHILD or size is None:
            break
        nxt = body + size
        if nxt > limit or nxt < body:
            break
        pos = nxt
    return pos


def cluster_end(data, start, limit):
    """End of a Cluster with unknown size: first element that is not a
    Cluster child (RFC 8794 section 4.3, RFC 9559 section 5.1.3)."""
    pos = start
    while pos < limit:
        try:
            eid, body, size = element(data, pos)
        except EbmlError:
            # The recording was cut inside an element header.
            break
        if eid not in CLUSTER_CHILD:
            break
        if size is None:
            stop = group_end(data, body, limit) if eid == 0xA0 else limit
            pos = stop
            if stop <= body:
                break
            continue
        nxt = body + size
        if nxt > limit or nxt < body:
            # The recording was cut inside this element; keep what we have.
            break
        pos = nxt
    return pos


def segment_elements(data, start, end, unknown=False):
    """Yield (id, body_start, body_end) for Segment children, resolving
    unknown-sized Segments and Clusters (RFC 8794 section 4.3)."""
    pos = start
    while pos < end:
        try:
            eid, body, size = element(data, pos)
        except EbmlError:
            # A live recording may end in the middle of an element header.
            return
        if unknown and eid in (SEGMENT, 0x1A45DFA3):
            # A new Segment (or EBML header) ends an unknown-sized Segment.
            return
        if size is None:
            stop = cluster_end(data, body, end) if eid == CLUSTER else end
            if stop <= pos:
                return
            yield eid, body, stop
            pos = stop
            continue
        stop = body + size
        if stop > end or stop < body:
            # The recording was cut inside this element.  A Cluster is still
            # worth reading up to the end of the data we have.
            if eid == CLUSTER and body < end:
                yield eid, body, end
            return
        yield eid, body, stop
        pos = stop


def parse_block(data, start, end, stats, tracks, cts, key=None):
    """Record one SimpleBlock/Block.  ``key`` overrides the SimpleBlock key
    flag (BlockGroup keyframes come from the absence of ReferenceBlock)."""
    try:
        tr, rel, flags, sizes, fstart = block(data, start, end)
        encodings = tracks.get(tr, {}).get("encodings", ())
        nbytes = frame_bytes(data, fstart, sizes, encodings)
    except (EbmlError, struct.error, IndexError):
        # Truncated or malformed block at the tail of a live recording.
        return False
    if key is None:
        key = bool(flags & 0x80)
    record(stats, tr, cts + rel, nbytes, len(sizes), key)
    return True


def read_cluster(data, start, end, stats, tracks):
    cts = 0
    pos = start
    while pos < end:
        try:
            cid, cb, size = element(data, pos)
        except EbmlError:
            return
        if size is None:
            # Only a BlockGroup may legally have an unknown size here; its
            # extent is the run of BlockGroup children that follows.
            if cid != 0xA0:
                return
            cf = group_end(data, cb, end)
        else:
            cf = cb + size
            if cf > end or cf < cb:
                return
        if cid == 0xE7:
            cts = uint(data[cb:cf])
        elif cid == 0xA3:
            if not parse_block(data, cb, cf, stats, tracks, cts):
                return
        elif cid == 0xA0:
            # A BlockGroup Block is a keyframe when it has no ReferenceBlock.
            key, blk = True, None
            try:
                for gid, gb, gf in children(data, cb, cf):
                    if gid == 0xFB:
                        key = False
                    elif gid == 0xA1 and blk is None:
                        blk = (gb, gf)
            except EbmlError:
                pass
            if blk is not None:
                if not parse_block(data, blk[0], blk[1], stats, tracks, cts, key):
                    return
        pos = cf


def scan(data):
    eid, b, size = element(data, 0)
    if eid != 0x1A45DFA3:
        raise EbmlError("not an EBML file")
    if size is None:
        raise EbmlError("EBML header has unknown size")
    pos = b + size
    if pos > len(data):
        raise EbmlError("truncated EBML header")
    # Global elements (Void, CRC-32) may sit between the header and Segment.
    while pos < len(data):
        eid, seg, size = element(data, pos)
        if eid == SEGMENT:
            break
        if eid not in (0xEC, 0xBF) or size is None:
            raise EbmlError("no Segment")
        pos = seg + size
    else:
        raise EbmlError("no Segment")
    if size is None:
        seg_end = len(data)
        unknown = True
    else:
        seg_end = seg + size
        if seg_end > len(data):
            # A live recording may be cut short of its declared Segment end.
            seg_end = len(data)
        unknown = False

    # First pass: Info and Tracks may follow the Clusters in a stream.
    scale, duration, tracks = 1000000, None, {}
    for eid, body, end in segment_elements(data, seg, seg_end, unknown):
        if eid == INFO:
            scale, duration = read_info(data, body, end)
        elif eid == TRACKS:
            tracks = read_tracks(data, body, end)

    stats = {}
    clusters = 0
    for eid, body, end in segment_elements(data, seg, seg_end, unknown):
        if eid == CLUSTER:
            clusters += 1
            read_cluster(data, body, end, stats, tracks)

    out = []
    for num in sorted(tracks):
        t = tracks[num]
        s = stats.get(num, {"blocks": 0, "frames": 0, "keyframes": 0, "bytes": 0, "first": None, "last": None})
        ms = lambda v: None if v is None else str(Fraction(v * scale, 1000000))
        out.append({**{k: v for k, v in t.items() if k != "encodings"},
                    "blocks": s["blocks"], "frames": s["frames"], "keyframes": s["keyframes"], "bytes": s["bytes"],
                    "first_ms": ms(s["first"]), "last_ms": ms(s["last"])})
    return {"timestamp_scale": scale, "duration_ms": None if duration is None else round(duration * scale / 1000000, 3),
            "clusters": clusters, "tracks": out}


def record(stats, track, ts, nbytes, nframes, key):
    s = stats.setdefault(track, {"blocks": 0, "frames": 0, "keyframes": 0, "bytes": 0, "first": None, "last": None})
    s["blocks"] += 1
    s["frames"] += nframes
    s["keyframes"] += 1 if key else 0
    s["bytes"] += nbytes
    s["first"] = ts if s["first"] is None else min(s["first"], ts)
    s["last"] = ts if s["last"] is None else max(s["last"], ts)

