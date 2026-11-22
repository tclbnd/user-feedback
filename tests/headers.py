"""Builders for small synthetic Monkey's Audio headers used by the tests."""
import struct

FLAG_8BIT, FLAG_CRC, FLAG_PEAK, FLAG_24BIT, FLAG_SEEK, FLAG_NO_WAV = 1, 2, 4, 8, 16, 32


def new(version=3990, level=2000, bpf=73728 * 4, final=12345, frames=10, bits=16, ch=2, rate=44100,
        frame_bytes=5_000_000, flags=0, desc_extra=0, seek=None, wav=44, id3=b""):
    """An APE descriptor file (3.98+)."""
    seek = frames if seek is None else seek
    desc_bytes = 52 + desc_extra
    desc = b"MAC " + struct.pack("<HHIIIIIII", version, 0, desc_bytes, 24, seek * 4, wav,
                                 frame_bytes & 0xFFFFFFFF, frame_bytes >> 32, 0) + bytes(range(16)) + bytes(desc_extra)
    head = struct.pack("<HHIIIHHI", level, flags, bpf, final, frames, bits, ch, rate)
    return id3 + desc + head + bytes(seek * 4) + bytes(wav) + bytes(1000)


def old(version=3800, level=4000, ch=2, rate=44100, frames=37, final=30111, flags=FLAG_PEAK,
        wav=44, peak=31873, seek=None, bits=None):
    """A legacy single-header file (pre-3.98).

    `seek` is the stored seek element count; None leaves the flag clear, meaning
    the table is implied to hold one entry per frame.
    """
    if bits == 8:
        flags |= FLAG_8BIT
    elif bits == 24:
        flags |= FLAG_24BIT
    if seek is not None:
        flags |= FLAG_SEEK
    head = struct.pack("<HHHHIIIII", version, level, flags, ch, rate, wav, 0, frames, final)
    extra = struct.pack("<I", peak) if flags & FLAG_PEAK else b""
    if seek is not None:
        extra += struct.pack("<I", seek)
    return b"MAC " + head + extra + bytes((seek or 0) * 4) + bytes(wav)


def id3v2(body=b"", version=4, footer=False):
    """An ID3v2 tag, sized the way a real one is (the syncsafe size excludes header and footer)."""
    size = len(body)
    syncsafe = bytes(((size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F))
    tag = b"ID3" + bytes([version, 0, 0x10 if footer else 0]) + syncsafe + body
    if footer:
        tag += b"3DI" + bytes([version, 0, 0x10]) + syncsafe
    return tag



