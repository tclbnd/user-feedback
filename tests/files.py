"""Builders for small synthetic RealMedia files used by the tests."""
import struct


def chunk(cid, body, ver=0):
    return cid + struct.pack(">IH", len(body) + 10, ver) + body


def ps(s, width=1):
    b = s.encode("latin-1")
    return len(b).to_bytes(width, "big") + b


def ra(version=5, rate=44100, bits=16, channels=2, codec="cook"):
    body = struct.pack(">H", 0) + (b".ra4" if version == 4 else b".ra5") + struct.pack(">IHIHI", 0, version, 0x4E, 3, 600)
    body += bytes(12) + struct.pack(">HHHH", 14, 600, 300, 0)
    if version == 5:
        body += bytes(6)
    body += struct.pack(">HHHH", rate, 0, bits, channels)
    if version == 4:
        body += ps("Int4") + ps(codec)
    else:
        body += b"genr" + codec.encode()
    return b".ra\xfd" + struct.pack(">H", version) + body + bytes(10)


def rv(codec="RV40", w=640, h=480, fps=25):
    body = b"VIDO" + codec.encode() + struct.pack(">HHHHI", w, h, 12, 0, int(round(fps * 65536))) + bytes(8)
    return struct.pack(">I", len(body) + 4) + body


def mdpr(num, mime, ts, name="", duration=60000):
    body = struct.pack(">H7I", num, 64000, 48000, 1000, 600, 0, 3000, duration) + ps(name) + ps(mime)
    body += struct.pack(">I", len(ts)) + ts
    return chunk(b"MDPR", body)


def prop(duration_ms=60000, preroll=3000, streams=2, avg=96000):
    return chunk(b"PROP", struct.pack(">9IHH", 128000, avg, 1000, 600, 500, duration_ms, preroll, 0, 0, streams, 9))


def cont(title="", author="", copyright="", comment=""):
    return chunk(b"CONT", b"".join(ps(x, 2) for x in (title, author, copyright, comment)))


def packet(stream, ts, key=False, size=40):
    version = 0
    if version == 0:
        head = struct.pack(">HHHIBB", 0, 12 + size, stream, ts, 0, 2 if key else 0)
    else:
        head = struct.pack(">HHHIHB", 1, 13 + size, stream, ts, 0, 2 if key else 0)
    return head + bytes(size)


def data(packets):
    return chunk(b"DATA", struct.pack(">II", len(packets), 0) + b"".join(packets))


def rm(*chunks_):
    return chunk(b".RMF", struct.pack(">II", 0, len(chunks_) + 1)) + b"".join(chunks_)


def clip(duration_ms=60000, audio=None, video=None, title="", author="", n=10):
    pver, fileinfo, index, split = 0, False, False, False
    streams, pk = [], []
    if audio is not None:
        streams.append(mdpr(0, "audio/x-pn-realaudio", audio, "Audio Stream", duration_ms))
    if video is not None:
        streams.append(mdpr(len(streams), "video/x-pn-realvideo", video, "Video Stream", duration_ms))
    if fileinfo:
        streams.append(mdpr(len(streams), "logical-fileinfo", bytes(40), ""))
    for i in range(n):
        for s in range(2 if (audio is not None and video is not None) else 1):
            pk.append(packet(s, i * 40, key=(s == 1 and i % 5 == 0) or (s == 0 and audio is None and i % 5 == 0)))
    dchunks = [data(pk)] if not split else [data(pk[:len(pk) // 2]), data(pk[len(pk) // 2:])]
    return rm(prop(duration_ms, 3000, len(streams)), cont(title, author, "(c) 1999", ""), *streams, *dchunks,
              )
