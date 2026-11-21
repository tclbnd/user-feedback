"""SCTE 35 section builder."""
import base64


def _table():
    out = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else c << 1
        out.append(c & 0xFFFFFFFF)
    return out


_T = _table()


def crc32(data):
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _T[(c >> 24) ^ b]
    return c


class W:
    def __init__(self):
        self.bits = []

    def put(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)
        return self

    def raw(self, b):
        for x in b:
            self.put(x, 8)
        return self

    def data(self):
        assert len(self.bits) % 8 == 0, len(self.bits)
        return bytes(int("".join(map(str, self.bits[i:i + 8])), 2) for i in range(0, len(self.bits), 8))


def stime(w, pts):
    if pts is None:
        w.put(0, 1).put(0x7F, 7)
    else:
        w.put(1, 1).put(0x3F, 6).put(pts, 33)


def insert(event_id=1, cancel=False, out=True, immediate=False, pts=0, components=None, duration=None, auto=True, upid=1, avail=0, expected=0):
    w = W().put(event_id, 32).put(1 if cancel else 0, 1).put(0x7F, 7)
    if cancel:
        return 0x05, w.data()
    w.put(1 if out else 0, 1).put(0 if components is not None else 1, 1).put(1 if duration is not None else 0, 1).put(1 if immediate else 0, 1).put(0xF, 4)
    if components is None:
        if not immediate:
            stime(w, pts)
    else:
        w.put(len(components), 8)
        for tag, cpts in components:
            w.put(tag, 8)
            if not immediate:
                stime(w, cpts)
    if duration is not None:
        w.put(1 if auto else 0, 1).put(0x3F, 6).put(duration, 33)
    w.put(upid, 16).put(avail, 8).put(expected, 8)
    return 0x05, w.data()


def time_signal(pts):
    w = W()
    stime(w, pts)
    return 0x06, w.data()


def null():
    return 0x00, b""


def private(ident=b"ACME", body=b"\x01\x02"):
    return 0xFF, ident + body


def seg(event_id=7, cancel=False, upid_type=0x09, upid=b"SIGNAL:abc", type_id=0x34, num=1, expected=1, duration=None,
        dnr=True, restrict=(1, 1, 1, 3), components=None, sub=None):
    w = W().put(event_id, 32).put(1 if cancel else 0, 1).put(0x7F, 7)
    if not cancel:
        w.put(0 if components is not None else 1, 1).put(1 if duration is not None else 0, 1).put(1 if dnr else 0, 1)
        if dnr:
            w.put(0x1F, 5)
        else:
            a, b, c, d = restrict
            w.put(a, 1).put(b, 1).put(c, 1).put(d, 2)
        if components is not None:
            w.put(len(components), 8)
            for tag, off in components:
                w.put(tag, 8).put(0x7F, 7).put(off, 33)
        if duration is not None:
            w.put(duration, 40)
        w.put(upid_type, 8).put(len(upid), 8).raw(upid)
        w.put(type_id, 8).put(num, 8).put(expected, 8)
        if sub is not None:
            w.put(sub[0], 8).put(sub[1], 8)
    return bytes([0x02]) + bytes([4 + len(w.data())]) + b"CUEI" + w.data()


def avail(pid=0x1234):
    return bytes([0x00, 8]) + b"CUEI" + pid.to_bytes(4, "big")


def foreign(tag=0x80, ident=b"XYZW", body=b"\x00" * 3):
    return bytes([tag, 4 + len(body)]) + ident + body


def section(command, descs=b"", adj=0, tier=0xFFF, legacy_len=False, encrypted=False, bad_crc=False, sap=3, version=0):
    ctype, cbody = command
    w = W().put(0xFC, 8).put(0, 1).put(0, 1).put(sap, 2)
    rest = W().put(version, 8).put(1 if encrypted else 0, 1).put(0, 6).put(adj, 33).put(0, 8).put(tier, 12)
    rest.put(0xFFF if legacy_len else len(cbody), 12).put(ctype, 8).raw(cbody).put(len(descs), 16).raw(descs)
    body = rest.data()
    length = len(body) + 4
    w.put(length, 12)
    sec = w.data() + body
    c = crc32(sec)
    if bad_crc:
        c ^= 1
    return sec + c.to_bytes(4, "big")


def b64(sec):
    return base64.b64encode(sec).decode()


def hexline(sec):
    return "0x" + sec.hex().upper()
