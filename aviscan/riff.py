"""RIFF chunk tree."""
from collections import namedtuple

Chunk = namedtuple("Chunk", "id offset size list_type children")


class AviError(ValueError):
    pass


def walk(data, start, end):
    """Return the chunks between *start* and *end*.

    RIFF chunks are word aligned: a chunk with an odd size is followed by one
    padding byte.  Ignoring that byte shifts every following chunk by one and
    makes the rest of the file unreadable.  The 'movi' list is not descended
    into because it may contain millions of chunks; its position and size are
    all callers need.
    """
    out = []
    start = max(0, start)
    end = min(end, len(data))
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4].decode("latin-1")
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid in ("RIFF", "LIST"):
            ltype = data[pos + 8:pos + 12].decode("latin-1") if pos + 12 <= end else ""
            stop = min(pos + 8 + size, end)
            kids = [] if ltype == "movi" else walk(data, pos + 12, stop)
            out.append(Chunk(cid, pos, size, ltype, kids))
        else:
            out.append(Chunk(cid, pos, size, None, []))
        pos += 8 + size
        if size & 1:
            pos += 1
    return out


def find(chunks, cid, list_type=None):
    for c in chunks:
        if c.id == cid and (list_type is None or c.list_type == list_type):
            return c
    return None


def find_all(chunks, cid, list_type=None):
    return [c for c in chunks
            if c.id == cid and (list_type is None or c.list_type == list_type)]


def segments(data):
    """Top-level RIFF chunks of an AVI file.

    Long recordings are split into several RIFF chunks ('AVI ', 'AVIX', ...).
    Some recorders write a bogus size on such a chunk (0, 0xFFFFFFFF, or the
    size of the first segment repeated), so an inconsistent size is treated
    as "runs to the end of the file" instead of losing the rest of the file.
    """
    out = []
    total = len(data)
    pos = 0
    while pos + 12 <= total:
        if data[pos:pos + 4] != b"RIFF":
            nxt = data.find(b"RIFF", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        end = pos + 8 + size
        if size < 4 or end > total:
            end = total
        ltype = data[pos + 8:pos + 12].decode("latin-1")
        out.append(Chunk("RIFF", pos, end - pos - 8, ltype,
                         walk(data, pos + 12, end)))
        pos = end
        if size & 1:
            pos += 1
    return out


def flatten(chunks):
    """Split the segment list so every chunk list belongs to one RIFF."""
    out = []
    for c in chunks:
        if c.id != "RIFF":
            continue
        out.append(c)
        kids = [k for k in c.children if k.id == "RIFF"]
        if kids:
            out.extend(flatten(kids))
    return out
