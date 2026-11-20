"""IVF container."""
from collections import namedtuple

Frame = namedtuple("Frame", "pts data")


class IvfError(ValueError):
    pass


def read(data):
    if data[:4] != b"DKIF":
        raise IvfError("not an IVF file")
    u16 = lambda p: int.from_bytes(data[p:p + 2], "little")
    u32 = lambda p: int.from_bytes(data[p:p + 4], "little")
    head = {"fourcc": data[8:12].decode("latin-1"), "width": u16(12), "height": u16(14),
            "timebase": f"{u32(16)}/{u32(20)}", "frames": u32(24)}
    frames, pos = [], 32
    for _ in range(head["frames"]):
        size = u32(pos)
        pts = int.from_bytes(data[pos + 4:pos + 12], "little")
        frames.append(Frame(pts, data[pos + 12:pos + 12 + size]))
        pos += 12 + size
    return head, frames
