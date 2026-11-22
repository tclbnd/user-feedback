"""Builders for small synthetic MXF files used by the tests."""
import struct
from fractions import Fraction

H = bytes.fromhex
UL = {k: H(v) for k, v in {
    0x3C0A: "060e2b34010101010101150200000000", 0x4403: "060e2b34010101020601010405000000",
    0x4803: "060e2b34010101020601010402040000", 0x4B01: "060e2b34010101020530040500000000",
    0x0202: "060e2b34010101020702020101020000", 0x0201: "060e2b34010101020407010000000000",
    0x3001: "060e2b34010101010406010100000000", 0x3002: "060e2b34010101020406010200000000",
    0x3202: "060e2b34010101010401050201010000", 0x3203: "060e2b34010101010401050202020000",
    0x320C: "060e2b34010101010401030102010400", 0x3301: "060e2b34010101020401050300000000",
    0x3D03: "060e2b34010101050402030101010000", 0x3D07: "060e2b34010101050402010104000000",
    0x3D01: "060e2b34010101040402030304000000", 0x3F01: "060e2b34010101040601010406000000",
    0x3F0B: "060e2b34010101050530040600000000", 0x3F0C: "060e2b34010101050702010301040000",
    0x3F0D: "060e2b34010101050702020101020000", 0x3F05: "060e2b34010101040406020100000000",
    0x3F06: "060e2b34010101040106020100000000", 0x3F07: "060e2b34010101040106010300000000",
}.items()}
PICTURE = H("060e2b34040101010103020201000000")
SOUND = H("060e2b34040101010103020202000000")
OP = {"OP1a": (1, 1), "OP1b": (1, 2), "OP2a": (2, 1), "OP3c": (3, 3), "OP-Atom": (0x10, 0)}


def ber(n, width=None):
    if width is None and n < 0x80:
        return bytes([n])
    width = width or 4
    return bytes([0x80 | width]) + n.to_bytes(width, "big")


def klv(key, value, width=None):
    return key + ber(len(value), width) + value


def u32(v):
    return v.to_bytes(4, "big")


def u64(v, signed=False):
    return v.to_bytes(8, "big", signed=signed)


def rat(fr):
    fr = Fraction(fr)
    return u32(fr.numerator) + u32(fr.denominator)


def batch(items, size=16):
    return u32(len(items)) + u32(size) + b"".join(items)


def uid(n):
    return bytes([0x55] * 12) + n.to_bytes(4, "big")


def partition(kind="header", status=4, body_sid=0, index_sid=0, op="OP1a"):
    k = {"header": 2, "body": 3, "footer": 4}[kind]
    key = H("060e2b34020501010d010201010000") [:13] + bytes([k, status, 0])
    o = OP[op]
    opul = H("060e2b34040101010d010201") + bytes(o) + b"\x09\x00"
    if op == "OP-Atom":
        opul = H("060e2b34040101020d01020110000000")
    body = struct.pack(">HHI", 1, 3, 1) + u64(0) * 5 + u32(index_sid) + u64(0) + u32(body_sid) + opul + batch([])
    return klv(key, body)


def std_partition(kind="header", status=1, body_sid=0, index_sid=0, op="OP1a"):
    """Partition pack laid out exactly as SMPTE ST 377-1 Annex G."""
    k = {"header": 2, "body": 3, "footer": 4}[kind]
    key = H("060e2b34020501010d01020101040000")[:14] + bytes([k, status])
    opul = (H("060e2b34040101020d01020110000000") if op == "OP-Atom"
            else H("060e2b34040101010d010201") + bytes(OP[op]) + b"\x09\x00")
    body = (struct.pack(">HHI", 1, 3, 1) + u64(0) * 4 + u32(index_sid) + u64(0)
            + u32(body_sid) + opul + batch([]))
    return klv(key, body)


def dynamic_primer(tagmap):
    """Primer pack whose entries rename some static local tags to dynamic ones."""
    items = [struct.pack(">H", tagmap.get(t, t)) + ul for t, ul in UL.items()]
    return klv(H("060e2b34020501010d01020101050100"), batch(items, 18))


def fill(n=20, version=2):
    return klv(H("060e2b34010101") + bytes([version]) + H("0301021001000000"), bytes(n))


def primer():
    items = [struct.pack(">H", t) + ul for t, ul in UL.items()]
    return klv(H("060e2b34020501010d01020101050100"), batch(items, 18))


def local_set(kind, props, tagmap=None):
    """props: list of (tag, bytes); tagmap renames static tags to dynamic ones."""
    key = H("060e2b34025301010d01010101") + struct.pack(">H", kind) + b"\x00"
    body = b""
    for t, v in props:
        t = (tagmap or {}).get(t, t)
        body += struct.pack(">HH", t, len(v)) + v
    return klv(key, body)


def header_metadata(rate=25, duration=250, video=(1920, 1080, 0, 10), audio=((48000, 2, 24),),
                    tagmap=None, open_duration=False):
    """video: (width, stored_height, frame_layout, depth); returns primer + sets."""
    sets = []
    n = [0]

    def new():
        n[0] += 1
        return uid(n[0])
    dur = u64(-1, True) if open_duration else u64(duration)
    mp_tracks, sp_tracks = [], []
    for defn, tracks, d in ((PICTURE, mp_tracks, duration), (PICTURE, sp_tracks, duration)):
        seq, trk = new(), new()
        sets.append(local_set(0x0F00, [(0x3C0A, seq), (0x0201, defn), (0x0202, u64(d) if not open_duration else u64(-1, True))]))
        sets.append(local_set(0x3B00, [(0x3C0A, trk), (0x4B01, rat(rate)), (0x4803, seq)]))
        tracks.append(trk)
    for _ in audio:
        seq, trk = new(), new()
        sets.append(local_set(0x0F00, [(0x3C0A, seq), (0x0201, SOUND), (0x0202, u64(duration * 1920))]))
        sets.append(local_set(0x3B00, [(0x3C0A, trk), (0x4B01, rat(48000)), (0x4803, seq)]))
        mp_tracks.append(trk)
    descs = []
    if video:
        w, h, layout, depth = video
        d = new()
        descs.append(d)
        sets.append(local_set(0x2800, [(0x3C0A, d), (0x3001, rat(rate)), (0x3002, u64(duration)), (0x3203, u32(w)),
                                       (0x3202, u32(h)), (0x320C, bytes([layout])), (0x3301, u32(depth))], tagmap))
    for sr, ch, bits in audio:
        d = new()
        descs.append(d)
        sets.append(local_set(0x4800, [(0x3C0A, d), (0x3D03, rat(sr)), (0x3D07, u32(ch)), (0x3D01, u32(bits))], tagmap))
    if len(descs) > 1:
        sets.append(local_set(0x4400, [(0x3C0A, new()), (0x3001, rat(rate)), (0x3F01, batch(descs))], tagmap))
    sets.insert(0, local_set(0x3700, [(0x3C0A, new()), (0x4403, batch(sp_tracks))]))
    sets.append(local_set(0x3600, [(0x3C0A, new()), (0x4403, batch(mp_tracks))]))
    return sets


def index_segment(start=0, duration=250, rate=25, cbe=0, index_sid=1, body_sid=1):
    body = b"".join(struct.pack(">HH", t, len(v)) + v for t, v in (
        (0x3C0A, uid(900 + start)), (0x3F0B, rat(rate)), (0x3F0C, u64(start)), (0x3F0D, u64(duration)),
        (0x3F05, u32(cbe)), (0x3F06, u32(index_sid)), (0x3F07, u32(body_sid))))
    return klv(H("060e2b34025301010d01020101100100"), body)


def element(kind="picture", n=1, size=64, width=None):
    item = {"picture": 0x15, "sound": 0x16, "data": 0x17, "compound": 0x18, "system": 0x14}[kind]
    return klv(H("060e2b34010201010d010301") + bytes([item, 1, 1, n]), bytes(size), width)


def rip(count=2):
    return klv(H("060e2b34020501010d01020101110100"), bytes(12 * count) + u32(0))


def mxf(header_sets, frames=10, audio=1, index=None):
    op, header_status, footer_sets, run_in, width, extra, body = "OP1a", 4, None, b"", None, b"", False
    out = run_in + partition("header", header_status, 0, 0, op) + fill()
    out += primer() + b"".join(header_sets)
    for i in range(frames):
        out += element("system", 1, 28, width) + element("picture", 1, 200, width)
        for a in range(audio):
            out += element("sound", a + 1, 96, width)
    out += extra
    if body:
        out += partition("body", 4, 1, 1, op) + b"".join(index or [])
    out += partition("footer", 4, 0, 1, op)
    if footer_sets:
        out += primer() + b"".join(footer_sets)
    if not body:
        out += b"".join(index or [])
    return out + rip()
