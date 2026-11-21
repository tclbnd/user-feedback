"""Builders for small synthetic Wave64 files used by the tests."""
import struct
import uuid


def guid(text):
    return uuid.UUID(text).bytes_le


RIFF = guid("66666972-912E-11CF-A5D6-28DB04C10000")
WAVE = guid("65766177-ACF3-11D3-8CD1-00C04F8EDB8A")
FMT = guid("20746D66-ACF3-11D3-8CD1-00C04F8EDB8A")
DATA = guid("61746164-ACF3-11D3-8CD1-00C04F8EDB8A")
LIST = guid("7473696C-912F-11CF-A5D6-28DB04C10000")
JUNK = guid("6B6E756A-ACF3-11D3-8CD1-00C04F8EDB8A")
SUB_PCM = guid("00000001-0000-0010-8000-00AA00389B71")
SUB_FLOAT = guid("00000003-0000-0010-8000-00AA00389B71")


def chunk(g, body):
    size = 24 + len(body)
    return g + struct.pack("<Q", size) + body + bytes((-size) % 8)


def fmt(channels=2, rate=48000, bits=16, tag=1, container=None, sub=None):
    container = container or bits
    align = channels * container // 8
    body = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, container)
    if tag == 0xFFFE:
        body += struct.pack("<HHI", 22, bits, 0) + (sub or SUB_PCM)
    return chunk(FMT, body)


def w64(*chunks_):
    body = WAVE + b"".join(chunks_)
    return RIFF + struct.pack("<Q", 24 + len(body)) + body


def pcm(frames, width=2, shift=0):
    out = bytearray()
    for fr in frames:
        for v in fr:
            out += (v << shift).to_bytes(width, "little", signed=True)
    return bytes(out)
