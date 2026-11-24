"""Synthetic RoQ movies used by the tests."""
import struct

HEADER, INFO, CODEBOOK, VQ, JPEG, MONO, STEREO, PACKET = 0x1084, 0x1001, 0x1002, 0x1011, 0x1012, 0x1020, 0x1021, 0x1030


def empty():
    """A header-only RoQ movie."""
    return struct.pack("<HIH", HEADER, 0xFFFFFFFF, 30)


def chunk(cid, size, arg):
    return struct.pack("<HIH", cid, size, arg) + bytes(size)


def movie(fps, chunks):
    """Build a movie whose chunks are (id, size, arg) triples."""
    out = [struct.pack("<HIH", HEADER, 0xFFFFFFFF, fps)]
    for cid, size, arg in chunks:
        out.append(chunk(cid, size, arg))
    return b"".join(out)


def rich():
    """One of every chunk, including a full 256-unit codebook and JPEG frames."""
    return movie(15, [
        (INFO, 8, 0),
        (CODEBOOK, 0, 0x0000),       # 256 2x2 and 256 4x4 units
        (CODEBOOK, 0, 0x0408),       # 4 2x2 and 8 4x4 units
        (VQ, 10, 0),
        (JPEG, 20, 0),
        (STEREO, 14, 0xFC02),        # 7 stereo sample frames, predictors -4 and 2
        (MONO, 5, 0xF800),           # 5 mono sample frames, predictor -2048
        (PACKET, 8, 0),
    ])


def truncated():
    """The last chunk claims more bytes than the file holds."""
    return movie(30, [(VQ, 100, 0)])[:-4]

