"""Builders for small synthetic Monkey's Audio headers used by the tests."""
import struct


def new(version=3990, level=2000, bpf=73728 * 4, final=12345, frames=10, bits=16, ch=2, rate=44100,
        frame_bytes=5_000_000, flags=0, id3=b""):
    seek, wav, term, desc_extra, md5, tail = None, 44, 0, 0, bytes(range(16)), 1000
    seek = frames if seek is None else seek
    desc_bytes = 52 + desc_extra
    desc = b"MAC " + struct.pack("<HHIIIIIII", version, 0, desc_bytes, 24, seek * 4, wav,
                                 frame_bytes & 0xFFFFFFFF, frame_bytes >> 32, term) + md5 + bytes(desc_extra)
    head = struct.pack("<HHIIIHHI", level, flags, bpf, final, frames, bits, ch, rate)
    return id3 + desc + head + bytes(seek * 4) + bytes(wav) + bytes(tail)


def old(version=3800, level=2000, frames=10, final=1234, wav=44, ch=2, rate=44100,
        flags=0, peak=None, seek=None, id3=b"", tail=400):
    """A pre-3.98 header: fixed fields, optional peak/count, WAV header, seek table."""
    if peak is not None:
        flags |= 4
    if seek is not None:
        flags |= 16
    head = b"MAC " + struct.pack("<HHHH", version, level, flags, ch)
    head += struct.pack("<IIIII", rate, wav, 0, frames, final)
    if peak is not None:
        head += struct.pack("<I", peak)
    if seek is not None:
        head += struct.pack("<I", seek)
    head += bytes(wav)
    n = frames if seek is None else seek
    head += b"".join(struct.pack("<I", 200 + i * 20) for i in range(n))
    return id3 + head + bytes(tail)


