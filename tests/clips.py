"""MP4 builder for sample-table tests."""
import struct


def box(kind, payload=b"", large=False):
    if large:
        return struct.pack(">I4sQ", 1, kind.encode(), len(payload) + 16) + payload
    return struct.pack(">I4s", len(payload) + 8, kind.encode()) + payload


def fbox(kind, version, flags, payload):
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def time_header(kind, scale, dur, v1=False):
    if kind == "mvhd":
        tail = struct.pack(">IH", 0x10000, 0x100) + bytes(10) + bytes(36) + bytes(24) + struct.pack(">I", 99)
    else:
        tail = struct.pack(">HH", 0x55C4, 0)
    if v1:
        return fbox(kind, 1, 0, struct.pack(">QQIQ", 0, 0, scale, dur) + tail)
    return fbox(kind, 0, 0, struct.pack(">IIII", 0, 0, scale, dur) + tail)


class Track:
    def __init__(self, tid=1, handler="vide", scale=90000, deltas=None, sizes=None, chunking=None,
                 sync=None, ctts=None, ctts_v1=False, elst=None, elst_v1=False, mdhd_v1=False, enabled=True,
                 uniform=False, co64=False):
        self.tid, self.handler, self.scale = tid, handler, scale
        self.sizes = sizes or [100] * 10
        self.deltas = deltas or [3000] * len(self.sizes)
        self.chunking = chunking or [len(self.sizes)]
        self.sync, self.ctts, self.ctts_v1 = sync, ctts, ctts_v1
        self.elst, self.elst_v1, self.mdhd_v1 = elst, elst_v1, mdhd_v1
        self.enabled, self.uniform, self.co64 = enabled, uniform, co64

    def chunks(self):
        out, i = [], 0
        for n in self.chunking:
            out.append(self.sizes[i:i + n])
            i += n
        return out

    def trak(self, offsets):
        stts, prev = [], None
        for d in self.deltas:
            if stts and stts[-1][1] == d:
                stts[-1][0] += 1
            else:
                stts.append([1, d])
        body = struct.pack(">I", len(stts)) + b"".join(struct.pack(">II", c, d) for c, d in stts)
        stbl = fbox("stsd", 0, 0, struct.pack(">I", 0))
        stbl += fbox("stts", 0, 0, body)
        runs = []
        for k, n in enumerate(self.chunking, 1):
            if not runs or runs[-1][1] != n:
                runs.append((k, n))
        stbl += fbox("stsc", 0, 0, struct.pack(">I", len(runs)) + b"".join(struct.pack(">III", f, n, 1) for f, n in runs))
        if self.uniform:
            stbl += fbox("stsz", 0, 0, struct.pack(">II", self.sizes[0], len(self.sizes)))
        else:
            stbl += fbox("stsz", 0, 0, struct.pack(">II", 0, len(self.sizes)) + b"".join(struct.pack(">I", s) for s in self.sizes))
        if self.co64:
            stbl += fbox("co64", 0, 0, struct.pack(">I", len(offsets)) + b"".join(struct.pack(">Q", o) for o in offsets))
        else:
            stbl += fbox("stco", 0, 0, struct.pack(">I", len(offsets)) + b"".join(struct.pack(">I", o) for o in offsets))
        if self.sync is not None:
            stbl += fbox("stss", 0, 0, struct.pack(">I", len(self.sync)) + b"".join(struct.pack(">I", s) for s in self.sync))
        if self.ctts is not None:
            f = ">Ii" if self.ctts_v1 else ">II"
            stbl += fbox("ctts", 1 if self.ctts_v1 else 0, 0, struct.pack(">I", len(self.ctts)) + b"".join(struct.pack(f, 1, o) for o in self.ctts))
        total = sum(self.deltas)
        mdia = time_header("mdhd", self.scale, total, self.mdhd_v1)
        mdia += fbox("hdlr", 0, 0, bytes(4) + self.handler.encode() + bytes(12) + b"h\0")
        mdia += box("minf", box("dinf", b"") + box("stbl", stbl))
        tkhd = fbox("tkhd", 0, 3 if self.enabled else 2, struct.pack(">IIIII", 0, 0, self.tid, 0, 0) + bytes(60))
        out = tkhd
        if self.elst is not None:
            if self.elst_v1:
                ent = b"".join(struct.pack(">QqI", d, m, 0x10000) for d, m in self.elst)
            else:
                ent = b"".join(struct.pack(">IiI", d, m, 0x10000) for d, m in self.elst)
            out += box("edts", fbox("elst", 1 if self.elst_v1 else 0, 0, struct.pack(">I", len(self.elst)) + ent))
        return box("trak", out + box("mdia", mdia))


def mp4(tracks, movie_scale=1000, movie_dur=None, mvhd_v1=False, moov_first=False, mdat_large=False,
        mdat_zero=False, base=0, extra_top=b""):
    """Chunks are interleaved track by track. base shifts chunk offsets (for co64 tests)."""
    ftyp = box("ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    order = []
    lists = [t.chunks() for t in tracks]
    for k in range(max(len(c) for c in lists)):
        for ti, c in enumerate(lists):
            if k < len(c):
                order.append((ti, c[k]))
    payload = b""
    rel = [[] for _ in tracks]
    for ti, sizes in order:
        rel[ti].append(len(payload))
        for j, s in enumerate(sizes):
            payload += bytes(((ti * 50 + j + n) & 0xFF) for n in range(s))
    dur = movie_dur if movie_dur is not None else max(sum(t.deltas) * movie_scale // t.scale for t in tracks)

    def build(mdat_start):
        traks = b"".join(t.trak([mdat_start + r + base for r in rel[i]]) for i, t in enumerate(tracks))
        return box("moov", time_header("mvhd", movie_scale, dur, mvhd_v1) + traks)

    head_len = 16 if mdat_large else 8
    if moov_first:
        moov = build(0)
        moov = build(len(ftyp) + len(extra_top) + len(moov) + head_len)
        mdat = box("mdat", payload, large=mdat_large)
        if mdat_zero:
            mdat = struct.pack(">I4s", 0, b"mdat") + payload
        return ftyp + extra_top + moov + mdat
    mdat = box("mdat", payload, large=mdat_large)
    moov = build(len(ftyp) + len(extra_top) + head_len)
    return ftyp + extra_top + mdat + moov
