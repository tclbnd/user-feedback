"""Builders for the synthetic RoQ movies the tests read.

Each chunk is an 8-byte preamble (16-bit id, 32-bit payload size, 16-bit
argument) followed by its payload, as id's engines read the format.
"""
import struct

HEADER, INFO, CODEBOOK, VQ, JPEG, MONO, STEREO, PACKET = 0x1084, 0x1001, 0x1002, 0x1011, 0x1012, 0x1020, 0x1021, 0x1030


def header(fps, size=0xFFFFFFFF, cid=HEADER):
    return struct.pack("<HIH", cid, size, fps)


def chunk(cid, body=b"", arg=0, size=None):
    if size is None:
        size = len(body)
    return struct.pack("<HIH", cid, size, arg & 0xFFFF) + body


def movie(fps, *chunks):
    return header(fps) + b"".join(chunks)


def empty():
    return header(30)


def info(width, height):
    return chunk(INFO, struct.pack("<HHHH", width, height, 8, 4))


def codebook(cells_2x2, cells_4x4):
    """A codebook chunk: 6-byte 2x2 cells and 4-byte 4x4 cells."""
    arg = ((cells_2x2 & 0xFF) << 8) | (cells_4x4 & 0xFF)
    return chunk(CODEBOOK, bytes(6 * cells_2x2 + 4 * cells_4x4), arg)


def vq(size=16):
    return chunk(VQ, bytes(size))


def jpeg(size=16):
    return chunk(JPEG, bytes(size))


def mono(samples, predictor=0):
    return chunk(MONO, bytes(samples), predictor)


def stereo(samples, left=0, right=0):
    return chunk(STEREO, bytes(2 * samples), ((left & 0xFF) << 8) | (right & 0xFF))


def packet(body=b""):
    return chunk(PACKET, body)
