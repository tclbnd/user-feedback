"""IVF container."""
from collections import namedtuple

Frame = namedtuple("Frame", "pts data")


class IvfError(ValueError):
    pass


def read(data):
    if len(data) < 32 or data[:4] != b"DKIF":
        raise IvfError("not an IVF file")
    u16 = lambda p: int.from_bytes(data[p:p + 2], "little")
    u32 = lambda p: int.from_bytes(data[p:p + 4], "little")
    # The frame payload does not necessarily start at offset 32: the header
    # length field at offset 6 says where it starts.
    header_len = u16(6)
    if header_len < 32 or header_len > len(data):
        raise IvfError(f"bad IVF header length {header_len}")
    # Offset 16 is the frame rate numerator (time base denominator), offset 20
    # the frame rate denominator (time base numerator): timebase = sec/tick.
    head = {"fourcc": data[8:12].decode("latin-1"), "width": u16(12), "height": u16(14),
            "timebase": f"{u32(20)}/{u32(16)}", "frames": u32(24)}
    # The frame count in the header may be 0 (unknown); walk the file instead.
    frames, pos = [], header_len
    while pos < len(data):
        if len(data) - pos < 12:
            raise IvfError(f"truncated frame header at offset {pos}")
        size = u32(pos)
        pts = int.from_bytes(data[pos + 4:pos + 12], "little")
        start = pos + 12
        if size > len(data) - start:
            raise IvfError(f"frame at offset {pos} runs past end of file")
        frames.append(Frame(pts, data[start:start + size]))
        pos = start + size
    return head, frames
