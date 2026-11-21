"""Builders for small synthetic Monkey's Audio headers used by the tests."""
import struct


def new(version=3990, level=2000, bpf=73728 * 4, final=12345, frames=10, bits=16, ch=2, rate=44100, frame_bytes=5_000_000):
    flags, seek, wav, term, desc_extra, md5, tail = 0, None, 44, 0, 0, bytes(range(16)), 1000
    seek = frames if seek is None else seek
    desc_bytes = 52 + desc_extra
    desc = b"MAC " + struct.pack("<HHIIIIIII", version, 0, desc_bytes, 24, seek * 4, wav,
                                 frame_bytes & 0xFFFFFFFF, frame_bytes >> 32, term) + md5 + bytes(desc_extra)
    head = struct.pack("<HHIIIHHI", level, flags, bpf, final, frames, bits, ch, rate)
    return desc + head + bytes(seek * 4) + bytes(wav) + bytes(tail)


