"""Independent MKV demuxer / verifier (does NOT import mkvrecover).

Purpose: validate repaired containers from the outside, the way an
independent demuxing tool would.  It implements just enough EBML to
enumerate tracks, clusters, blocks, laced frames, Cues and SeekHead, then
supports time-based seek emulation.
"""

import hashlib

# Element ids, copied from the Matroska spec (not from the package under test).
EBML = 0x1A45DFA3
SEGMENT = 0x18538067
SEEKHEAD = 0x114D9B74
SEEK = 0x4DBB
SEEKID = 0x53AB
SEEKPOS = 0x53AC
INFO = 0x1549A966
TIMESCALE = 0x2AD7B1
TRACKS = 0x1654AE6B
TRACKENTRY = 0xAE
TRACKNUM = 0xD7
TRACKTYPE = 0x83
CODECID = 0x86
CODECPRIV = 0x63A2
VIDEO = 0xE0
AUDIO = 0xE1
CLUSTER = 0x1F43B675
TIMECODE = 0xE7
SIMPLEBLOCK = 0xA3
BLOCKGROUP = 0xA0
BLOCK = 0xA1
REFBLOCK = 0xFB
CUES = 0x1C53BB6B
CUEPOINT = 0xBB
CUETIME = 0xB3
CUETRACKPOS = 0xB7
CUETRACK = 0xF7
CUECLUSTERPOS = 0xF1
CUERELPOS = 0xF0


class Damaged(Exception):
    pass


def vint(buf, off, keep_marker=False):
    if off >= len(buf):
        raise Damaged("vint past eof at %d" % off)
    first = buf[off]
    width = next((i + 1 for i in range(8) if first & (0x80 >> i)), 0)
    if not width or off + width > len(buf):
        raise Damaged("bad vint at %d" % off)
    if keep_marker:
        return width, int.from_bytes(buf[off:off + width], "big")
    value = first & ((1 << (8 - width)) - 1)
    for k in range(1, width):
        value = (value << 8) | buf[off + k]
    unknown = value == (1 << (7 * width)) - 1
    return width, (None if unknown else value)


def elements(buf, start, end):
    """Yield (id, header_off, payload_off, payload_end, unknown) tuples."""
    off = start
    while off < end:
        il, eid = vint(buf, off, keep_marker=True)
        sl, size = vint(buf, off + il)
        poff = off + il + sl
        unknown = size is None
        pend = end if unknown else poff + size
        if not unknown and pend > end:
            raise Damaged("element %x payload exceeds scope at %d"
                          % (eid, off))
        yield eid, off, poff, pend, unknown
        if unknown:
            return
        off = pend


def segment_elements(buf, start, end):
    """Segment-level walker that splits unknown-size Clusters at the next
    valid Cluster header, mirroring real resync behaviour."""
    off = start
    while off < end:
        il, eid = vint(buf, off, keep_marker=True)
        sl, size = vint(buf, off + il)
        poff = off + il + sl
        unknown = size is None
        if unknown:
            if eid != CLUSTER:
                yield eid, off, poff, end, True
                return
            nxt = _next_cluster(buf, poff, end)
            pend = nxt if nxt is not None else end
            yield eid, off, poff, pend, True
            off = pend
            continue
        pend = poff + size
        if pend > end:
            raise Damaged("element %x exceeds segment at %d" % (eid, off))
        yield eid, off, poff, pend, False
        off = pend


CLUSTER_CHILD_IDS = {0xE7, 0xA3, 0xA0, 0xBF, 0xEC, 0xA7, 0xAB, 0x5854,
                     0xAF}


def _next_cluster(buf, start, end):
    """End of an unknown-size Cluster: next id that is not a Cluster child."""
    off = start
    while off < end:
        try:
            il, eid = vint(buf, off, keep_marker=True)
            sl, size = vint(buf, off + il)
        except Damaged:
            return end
        if eid not in CLUSTER_CHILD_IDS:
            return off
        if size is None:
            return end
        child_end = off + il + sl + size
        if child_end > end:
            return end
        off = child_end
    return end


def signed_vint(buf, off):
    width, raw = vint(buf, off)
    return width, raw - (1 << (7 * width - 1))


def parse_block(buf, poff, pend):
    tnlen, track = vint(buf, poff)
    rel = int.from_bytes(buf[poff + tnlen:poff + tnlen + 2], "big",
                         signed=True)
    flags = buf[poff + tnlen + 2]
    lacing = (flags >> 1) & 3
    keyframe = bool(flags & 0x80)
    cur = poff + tnlen + 3
    frames = []
    if lacing == 0:
        frames.append((cur, pend - cur))
    else:
        count = buf[cur] + 1
        cur += 1
        if lacing == 2:                       # fixed
            total = pend - cur
            piece = total // count
            if total % count:
                raise Damaged("bad fixed lacing")
            frames = [(cur + i * piece, piece) for i in range(count)]
        else:
            sizes = []
            for i in range(count - 1):
                if lacing == 1:               # Xiph
                    total = 0
                    while True:
                        byte = buf[cur]
                        cur += 1
                        total += byte
                        if byte != 255:
                            break
                    sizes.append(total)
                else:                         # EBML
                    w, delta = signed_vint(buf, cur)
                    cur += w
                    sizes.append(delta if i == 0 else sizes[-1] + delta)
            sizes.append((pend - cur) - sum(sizes))
            for size in sizes:
                if size < 0:
                    raise Damaged("negative laced frame size")
                frames.append((cur, size))
                cur += size
            if cur != pend:
                raise Damaged("lacing size mismatch")
    return {
        "track": track, "rel": rel, "flags": flags, "lacing": lacing,
        "keyframe": keyframe, "frames": frames,
    }


class Demuxed:
    def __init__(self, path):
        self.path = path
        self.buf = open(path, "rb").read()
        self._parse()

    def _parse(self):
        buf = self.buf
        top = list(elements(buf, 0, len(buf)))
        if not top or top[0][0] != EBML:
            raise Damaged("no EBML header")
        seg = next((t for t in top if t[0] == SEGMENT), None)
        if seg is None:
            raise Damaged("no segment")
        _id, seg_hdr, seg_poff, seg_pend, _u = seg
        self.segment_payload = seg_poff
        self.tracks = {}
        self.blocks = []          # ordered, fully demuxed packets
        self.cues = []
        self.seekhead = {}
        self.clusters = []
        time_scale = 1_000_000
        for eid, hoff, poff, pend, unknown in segment_elements(buf, seg_poff,
                                                               seg_pend):
            if eid == INFO:
                for cid, _h, cp, cpe, _u in elements(buf, poff, pend):
                    if cid == TIMESCALE:
                        time_scale = int.from_bytes(buf[cp:cpe], "big")
            elif eid == TRACKS:
                for te_id, _th, tp, tpe, _u in elements(buf, poff, pend):
                    if te_id != TRACKENTRY:
                        continue              # CRC-32 / Void siblings
                    entry = {"num": None, "codec": None, "type": None,
                             "private": None, "video": False,
                             "audio": False, "offset": _th}
                    for cid, _h, cp, cpe, _u in elements(buf, tp, tpe):
                        if cid == TRACKNUM:
                            entry["num"] = int.from_bytes(buf[cp:cpe], "big")
                        elif cid == CODECID:
                            entry["codec"] = buf[cp:cpe].decode()
                        elif cid == TRACKTYPE:
                            entry["type"] = buf[cp]
                        elif cid == CODECPRIV:
                            entry["private"] = buf[cp:cpe]
                        elif cid == VIDEO:
                            entry["video"] = True
                        elif cid == AUDIO:
                            entry["audio"] = True
                    if entry["num"] is not None:
                        self.tracks[entry["num"]] = entry
            elif eid == CLUSTER:
                self._cluster(hoff, poff, pend, unknown)
            elif eid == CUES:
                for cp_id, _ch, cpp, cppe, _u in elements(buf, poff, pend):
                    if cp_id != CUEPOINT:
                        continue              # CRC-32 / Void siblings
                    cue = {"time": None, "track": None,
                           "cluster_pos": None, "rel_pos": None}
                    for cid, _h, cp, cpe, _u in elements(buf, cpp, cppe):
                        if cid == CUETIME:
                            cue["time"] = int.from_bytes(buf[cp:cpe], "big")
                        elif cid == CUETRACKPOS:
                            for tid, _th, tcp, tcpe, _u in elements(
                                    buf, cp, cpe):
                                val = int.from_bytes(buf[tcp:tcpe], "big")
                                if tid == CUETRACK:
                                    cue["track"] = val
                                elif tid == CUECLUSTERPOS:
                                    cue["cluster_pos"] = val
                                elif tid == CUERELPOS:
                                    cue["rel_pos"] = val
                    if cue["time"] is not None:
                        self.cues.append(cue)
            elif eid == SEEKHEAD:
                for se_id, _sh, sp, spe, _u in elements(buf, poff, pend):
                    if se_id != SEEK:
                        continue          # CRC-32 / Void are legal siblings
                    sid = spos = None
                    for cid, _h, cp, cpe, _u in elements(buf, sp, spe):
                        if cid == SEEKID:
                            _w, sid = vint(buf, cp, keep_marker=True)
                        elif cid == SEEKPOS:
                            spos = int.from_bytes(buf[cp:cpe], "big")
                    if sid is not None and spos is not None:
                        self.seekhead[sid] = spos
        self.time_scale = time_scale

    def _cluster(self, header_off, poff, pend, unknown):
        buf = self.buf
        cluster_rel = header_off - self.segment_payload
        tc = None
        block_rel = {}
        order = []
        for cid, ch, cp, cpe, cu in elements(buf, poff, pend):
            if cid == TIMECODE:
                tc = int.from_bytes(buf[cp:cpe], "big")
            elif cid == SIMPLEBLOCK:
                blk = parse_block(buf, cp, cpe)
                order.append((cid, ch, blk))
            elif cid == BLOCKGROUP:
                inner = None
                ref = False
                for gid, gh, gp, gpe, gu in elements(buf, cp, cpe):
                    if gid == BLOCK:
                        inner = parse_block(buf, gp, gpe)
                    elif gid == REFBLOCK:
                        ref = True
                if inner is not None:
                    inner["reference"] = ref
                    order.append((cid, ch, inner))
        if tc is None:
            raise Damaged("cluster without timecode at %d" % header_off)
        self.clusters.append({"header": header_off, "rel": cluster_rel,
                              "timecode": tc})
        for cid, ch, blk in order:
            rel_pos = ch - header_off
            frame_hash = hashlib.sha256()
            for fo, fl in blk["frames"]:
                frame_hash.update(buf[fo:fo + fl])
            self.blocks.append({
                "track": blk["track"],
                "cluster_tc": tc,
                "rel_time": blk["rel"],
                "time": tc + blk["rel"],
                "lacing": blk["lacing"],
                "nframes": len(blk["frames"]),
                "frame_sizes": [fl for _fo, fl in blk["frames"]],
                "sha256": frame_hash.hexdigest(),
                "cluster_rel": cluster_rel,
                "block_rel": rel_pos,
                "keyframe": blk.get("keyframe", False) or
                not blk.get("reference", True),
                "container": "SimpleBlock" if cid == SIMPLEBLOCK
                else "BlockGroup",
                "header_off": ch,
            })

    # -- seek emulation ----------------------------------------------------- #
    def seek(self, target_ms):
        """Pick the last Cue at or before target_ms for each video track."""
        target_ticks = int(target_ms * 1_000_000 / self.time_scale)
        video = [n for n, t in self.tracks.items() if t["video"]]
        result = {}
        for track in video:
            candidates = [c for c in self.cues
                          if c["track"] == track and c["time"] <= target_ticks]
            if not candidates:
                candidates = [c for c in self.cues if c["track"] == track]
            cue = max(candidates, key=lambda c: c["time"])
            # Both positions are relative to the Segment payload; the
            # relative position is measured from the Cluster element start,
            # so the absolute block offset is their plain sum.
            abs_block = self.segment_payload + cue["cluster_pos"] + \
                (cue["rel_pos"] or 0)
            # Validate that the cue lands on a real parseable object.
            _id_len, eid = vint(self.buf, abs_block, keep_marker=True)
            if eid not in (SIMPLEBLOCK, BLOCKGROUP):
                raise Damaged("cue target %d is not a block (id %x)"
                              % (abs_block, eid))
            first_block = next(b for b in self.blocks
                               if b["cluster_rel"] == cue["cluster_pos"]
                               and b["block_rel"] == (cue["rel_pos"] or 0)
                               and b["track"] == track)
            result[track] = {"cue": cue, "abs_offset": abs_block,
                             "first_block": first_block}
        return result

    def packets_by_track_time(self):
        keyed = {}
        for block in self.blocks:
            keyed.setdefault((block["track"], block["time"]), []).append(block)
        return keyed

    def validate_indexes(self):
        """Every SeekHead/Cue pointer must resolve to a parseable object."""
        problems = []
        for eid, pos in self.seekhead.items():
            abs_off = self.segment_payload + pos
            try:
                _w, got = vint(self.buf, abs_off, keep_marker=True)
            except Damaged:
                problems.append("SeekHead id %x -> unreadable at %d"
                                % (eid, abs_off))
                continue
            if got != eid:
                problems.append("SeekHead id %x -> found %x at %d"
                                % (eid, got, abs_off))
        for cue in self.cues:
            abs_cluster = self.segment_payload + cue["cluster_pos"]
            try:
                _w, got = vint(self.buf, abs_cluster, keep_marker=True)
            except Damaged:
                problems.append("Cue cluster pos unreadable at %d"
                                % abs_cluster)
                continue
            if got != CLUSTER:
                problems.append("Cue points to %x, not Cluster, at %d"
                                % (got, abs_cluster))
            abs_block = abs_cluster + (cue["rel_pos"] or 0)
            _w, bid = vint(self.buf, abs_block, keep_marker=True)
            if bid not in (SIMPLEBLOCK, BLOCKGROUP):
                problems.append("Cue rel pos %d lands on id %x"
                                % (cue["rel_pos"], bid))
        return problems
