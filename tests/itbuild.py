"""Synthetic Impulse Tracker modules for the tests.

The builder follows the on-disk IT layout: 192-byte header, orders, the
instrument/sample/pattern offset tables, then the sample headers, pattern
data and song message appended in whatever order is convenient.
"""
import struct


def u8(v):
    return bytes([v & 0xff])


def modern_sample(name=b"", length=0, flags=0, c5=8363, loop=(0, 0),
                  sustain=(0, 0), pointer=0):
    """An 80-byte IMPS header as written by IT 2.04 and later."""
    h = bytearray(80)
    h[0:4] = b"IMPS"
    h[4:4 + len(name)] = name
    h[20:20 + min(len(name), 26)] = name[:26]
    h[31] = flags
    struct.pack_into("<I", h, 40, length)
    struct.pack_into("<I", h, 44, loop[0])
    struct.pack_into("<I", h, 48, loop[1])
    struct.pack_into("<I", h, 56, c5)
    struct.pack_into("<I", h, 60, sustain[0])
    struct.pack_into("<I", h, 64, sustain[1])
    struct.pack_into("<I", h, 76, pointer)
    return bytes(h)


def legacy_sample(name=b"", length=0, flags=0, c5=8363, loop=(0, 0),
                  sustain=(0, 0), pointer=0):
    """The 64-byte IMPS layout used by IT 2.00 (still padded to 80 bytes)."""
    h = bytearray(80)
    h[0:4] = b"IMPS"
    h[4:4 + len(name)] = name
    h[20:20 + min(len(name), 26)] = name[:26]
    h[31] = flags
    struct.pack_into("<I", h, 48, length)
    struct.pack_into("<I", h, 52, loop[0])
    struct.pack_into("<I", h, 56, loop[1])
    struct.pack_into("<I", h, 60, c5)
    struct.pack_into("<I", h, 64, sustain[0])
    struct.pack_into("<I", h, 68, sustain[1])
    struct.pack_into("<I", h, 76, pointer)
    return bytes(h)


def pack_channel(mask, note=None, instrument=None, volpan=None,
                 command=None, info=None, last=False):
    out = bytearray()
    if mask <= 0x7f and last:
        # Single-byte mask; bit 7 also marks the end of the row.
        out.append(mask | 0x80)
    else:
        # Two-byte mask: first byte has bit 7 clear, the second carries the
        # high mask bits and (bit 7) the end-of-row marker.
        out.append(mask & 0x7f)
        out.append((mask >> 8) | (0x80 if last else 0))
    if note is not None:
        out.append(note)
    if instrument is not None:
        out.append(instrument)
    if volpan is not None:
        out.append(volpan)
    if command is not None:
        out.append(command)
        out.append(info if info is not None else 0)
    return bytes(out)


def packed_pattern(rows, row_channels, with_magic=True):
    """row_channels: list (one entry per row) of packed channel byte strings."""
    body = bytearray()
    for channels in row_channels:
        for channel in channels:
            body += channel
    head = bytearray()
    if with_magic:
        # "IMPP", then the 8-byte pattern header: length, rows, reserved.
        head += b"IMPP"
        head += struct.pack("<HHI", len(body), rows, 0)
    else:
        # Header without marker: length and rows only.
        head += struct.pack("<HH", len(body), rows)
    return bytes(head + body)

def module(orders=b"", samples=(), patterns=(), pans=(), message=b"",
           cwt=0x0214, cmwt=0x0214, flags=0x000d):
    ordnum = len(orders)
    insnum = 0
    smpnum = len(samples)
    patnum = len(patterns)
    h = bytearray(192)
    h[0:4] = b"IMPM"
    struct.pack_into("<8H", h, 32,
                     ordnum, insnum, smpnum, patnum, cwt, cmwt, flags, 0)
    h[48:53] = bytes([128, 48, 6, 125, 128])
    struct.pack_into("<HI", h, 54, len(message), 0)  # offset patched later
    for i, pan in enumerate(pans[:64]):
        h[64 + i] = pan

    table_size = ordnum + 4 * (insnum + smpnum + patnum)
    cursor = 192 + table_size
    smp_offsets = []
    for sample in samples:
        smp_offsets.append(cursor)
        cursor += len(sample)
    pat_offsets = []
    for pattern in patterns:
        if pattern is None:
            pat_offsets.append(0)
        else:
            pat_offsets.append(cursor)
            cursor += len(pattern)
    msg_offset = cursor if message else 0
    if message:
        struct.pack_into("<I", h, 56, msg_offset)

    out = bytearray(h)
    out += orders
    # IT order: instrument offsets, sample offsets, pattern offsets.
    out += b"".join(struct.pack("<I", off) for off in smp_offsets)
    out += b"".join(struct.pack("<I", off) for off in pat_offsets)
    for sample in samples:
        out += sample
    for pattern in patterns:
        if pattern is not None:
            out += pattern
    out += message
    return bytes(out)
