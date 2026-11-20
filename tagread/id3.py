"""ID3v2 tag and frame reading."""
from collections import namedtuple

Frame = namedtuple("Frame", "id flags body")


class TagError(ValueError):
    pass


def syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def read_tag(data):
    if data[:3] != b"ID3":
        raise TagError("no ID3v2 tag")
    major = data[3]
    if major not in (3, 4):
        raise TagError(f"unsupported ID3v2.{major}")
    flags = data[5]
    size = syncsafe(data[6:10])
    body = data[10:10 + size]
    frames = []
    pos = 0
    while pos + 10 <= len(body):
        fid = body[pos:pos + 4]
        if fid == b"\0\0\0\0":
            break
        fsize = int.from_bytes(body[pos + 4:pos + 8], "big")
        fflags = body[pos + 8:pos + 10]
        frames.append(Frame(fid.decode("latin-1"), fflags, body[pos + 10:pos + 10 + fsize]))
        pos += 10 + fsize
    return {"version": f"2.{major}", "size": 10 + size, "flags": flags}, frames
