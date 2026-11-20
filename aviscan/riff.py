"""RIFF chunk tree."""
from collections import namedtuple

Chunk = namedtuple("Chunk", "id offset size list_type children")


class AviError(ValueError):
    pass


def walk(data, start, end):
    out = []
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4].decode("latin-1")
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid in ("RIFF", "LIST"):
            ltype = data[pos + 8:pos + 12].decode("latin-1")
            kids = walk(data, pos + 12, min(pos + 8 + size, end)) if ltype != "movi" else []
            out.append(Chunk(cid, pos, size, ltype, kids))
        else:
            out.append(Chunk(cid, pos, size, None, []))
        pos += 8 + size
    return out


def find(chunks, cid, list_type=None):
    for c in chunks:
        if c.id == cid and (list_type is None or c.list_type == list_type):
            return c
    return None
