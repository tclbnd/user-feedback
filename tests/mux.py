"""Transport stream builder."""


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


def section(table_id, ext, body, version=0):
    length = 5 + len(body) + 4
    sec = bytes([table_id, 0xB0 | (length >> 8), length & 0xFF, ext >> 8, ext & 0xFF, 0xC1 | (version << 1), 0, 0]) + body
    return sec + crc32(sec).to_bytes(4, "big")


def pat(programs, tsid=1):
    body = b"".join(bytes([p >> 8, p & 0xFF, 0xE0 | (pid >> 8), pid & 0xFF]) for p, pid in programs)
    return section(0x00, tsid, body)


def pmt(program, pcr_pid, streams, info=b""):
    body = bytes([0xE0 | (pcr_pid >> 8), pcr_pid & 0xFF, 0xF0 | (len(info) >> 8), len(info) & 0xFF]) + info
    for st, pid, desc in streams:
        body += bytes([st, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0 | (len(desc) >> 8), len(desc) & 0xFF]) + desc
    return section(0x02, program, body)


def pts_bytes(v, prefix=2):
    return bytes([(prefix << 4) | (((v >> 30) & 7) << 1) | 1, (v >> 22) & 0xFF, (((v >> 15) & 0x7F) << 1) | 1, (v >> 7) & 0xFF, ((v & 0x7F) << 1) | 1])


def pes(stream_id, pts=None, dts=None, size=300):
    opt = b""
    flags = 0
    if pts is not None and dts is not None:
        flags = 0xC0
        opt = pts_bytes(pts, 3) + pts_bytes(dts, 1)
    elif pts is not None:
        flags = 0x80
        opt = pts_bytes(pts, 2)
    head = bytes([0x80, flags, len(opt)]) + opt
    data = bytes((i * 3) & 0xFF for i in range(size))
    body = head + data
    length = len(body) if stream_id != 0xE0 else 0
    return b"\0\0\1" + bytes([stream_id]) + length.to_bytes(2, "big") + body


class Mux:
    def __init__(self):
        self.cc = {}
        self.out = []

    def packet(self, pid, payload=b"", pusi=False, pcr=None, af=None, disc=False, tei=False, afc=None, cc=None, ra=False):
        if cc is None:
            c = self.cc.get(pid, -1)
            if payload or afc in (1, 3):
                c = (c + 1) & 15
                self.cc[pid] = c
            cc = max(c, 0)
        adapt = b""
        if pcr is not None or disc or af is not None or ra:
            flags = (0x80 if disc else 0) | (0x40 if ra else 0) | (0x10 if pcr is not None else 0)
            body = bytes([flags])
            if pcr is not None:
                base, ext = pcr // 300, pcr % 300
                body += (base >> 1).to_bytes(4, "big") + bytes([((base & 1) << 7) | 0x7E | (ext >> 8), ext & 0xFF])
            adapt = body
        room = 184
        if adapt or af is not None:
            room -= 1 + len(adapt)
        payload = payload[:room]
        stuff = room - len(payload)
        if stuff:
            if not adapt and af is None:
                adapt = bytes([0])
                stuff -= 2
                if stuff < 0:
                    adapt = b""
                    payload = payload[:182]
                    adapt = bytes([0])
                    stuff = 182 - len(payload) - 1
            adapt += b"\xff" * max(stuff, 0)
        if afc is None:
            afc = (2 if adapt or af is not None else 0) | (1 if payload else 0)
        head = bytes([0x47, (0x80 if tei else 0) | (0x40 if pusi else 0) | (pid >> 8), pid & 0xFF, (afc << 4) | cc])
        a = bytes([len(adapt)]) + adapt if afc & 2 else b""
        pkt = (head + a + payload).ljust(188, b"\xff")
        assert len(pkt) == 188, len(pkt)
        self.out.append(pkt)

    def psi(self, pid, sec, pointer_tail=b"", split=None):
        data = bytes([len(pointer_tail)]) + pointer_tail + sec
        if split:
            self.packet(pid, data[:split], pusi=True)
            rest = data[split:]
            while rest:
                self.packet(pid, rest[:184])
                rest = rest[184:]
        else:
            first = True
            while data:
                self.packet(pid, data[:184], pusi=first)
                data = data[184:]
                first = False

    def stream(self, pid, pes_bytes):
        first = True
        while pes_bytes:
            self.packet(pid, pes_bytes[:184], pusi=first)
            pes_bytes = pes_bytes[184:]
            first = False

    def data(self, m2ts=False, rs=False):
        if m2ts:
            return b"".join((i * 1000).to_bytes(4, "big") + p for i, p in enumerate(self.out))
        if rs:
            return b"".join(p + bytes(16) for p in self.out)
        return b"".join(self.out)


def simple(video_pts=(0, 3003, 6006, 9009), audio_pts=(0, 1920, 3840), pcr=True, **kw):
    m = Mux()
    m.psi(0, pat([(1, 0x100)]))
    m.psi(0x100, pmt(1, 0x101, [(0x1B, 0x101, b""), (0x0F, 0x102, b"\x0a\x04eng\x00")]))
    for i, v in enumerate(video_pts):
        if pcr:
            m.packet(0x101, pcr=(v + 1000) * 300 if v + 1000 < (1 << 33) else 0, af=True)
        m.stream(0x101, pes(0xE0, v, size=400))
        if i < len(audio_pts):
            m.stream(0x102, pes(0xC0, audio_pts[i], size=150))
    return m
