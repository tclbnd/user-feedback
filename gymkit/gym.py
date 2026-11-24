"""Sega Genesis / Mega Drive register logs (.gym).

A log is a stream of commands clocked out one per frame at 60 Hz:

    0x00                 wait one frame
    0x01 reg value       write to YM2612 port 0
    0x02 reg value       write to YM2612 port 1
    0x03 value           write to the PSG

A log dumped by Gens may carry a 0x1AC byte ``GYMX`` header with the song,
game, publisher, emulator, dumper and comment strings, the loop frame and the
size of the uncompressed stream.  A non-zero size means the stream is zlib
packed.
"""
import struct
import zlib
from fractions import Fraction

HEADER_SIZE = 0x1AC
FRAME_RATE = 60

YM_KEY_ON = 0x28
YM_DAC_DATA = 0x2A


class GymError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def _header(data):
    """Decode a GYMX header: the string fields, loop frame and data size."""
    head = {
        "song": text(data[0x04:0x24]),
        "game": text(data[0x24:0x44]),
        "publisher": text(data[0x44:0x64]),
        "emulator": text(data[0x64:0x84]),
        "dumper": text(data[0x84:0xA4]),
        "comment": text(data[0xA4:0x1A4]),
    }
    loop_frame, data_size = struct.unpack_from("<II", data, 0x1A4)
    head["loop_frame"] = loop_frame or None
    # The stored size is that of the decompressed stream; Gens writes 0 when
    # the log is left uncompressed.
    head["packed"] = bool(data_size)
    return head, loop_frame, data_size


def _unpack(body):
    try:
        return zlib.decompress(body)
    except zlib.error as exc:
        raise GymError("cannot unpack GYM data: %s" % exc)


def _key_on_channel(value):
    """FM channel (1-6) targeted by a YM2612 key-on register value.

    Bits 0-1 pick one of three channels, bit 2 selects the second bank.
    """
    return (value & 0x03) + 1 + (3 if value & 0x04 else 0)


def parse(data):
    head = None
    loop_frame = 0
    body = data
    if data[:4] == b"GYMX":
        if len(data) < HEADER_SIZE:
            raise GymError("truncated GYMX header")
        head, loop_frame, data_size = _header(data)
        body = data[HEADER_SIZE:]
        if data_size:
            body = _unpack(body)

    frames = port0 = port1 = psg = dac = 0
    keys = {}
    p, n = 0, len(body)
    while p < n:
        op = body[p]
        if op == 0x00:
            frames += 1
            p += 1
        elif op in (0x01, 0x02):
            if p + 3 > n:
                break
            reg, val = body[p + 1], body[p + 2]
            if op == 0x01:
                port0 += 1
                if reg == YM_DAC_DATA:
                    dac += 1
                elif reg == YM_KEY_ON and val & 0xF0:
                    ch = _key_on_channel(val)
                    keys[ch] = keys.get(ch, 0) + 1
            else:
                port1 += 1
            p += 3
        elif op == 0x03:
            # The PSG write is a single data byte, not a (register, value) pair.
            if p + 2 > n:
                break
            psg += 1
            p += 2
        else:
            raise GymError("unknown command 0x%02X at %d" % (op, p))

    return {
        "header": head,
        "frames": frames,
        "duration": str(Fraction(frames, FRAME_RATE)),
        "ym_port0_writes": port0,
        "ym_port1_writes": port1,
        "psg_writes": psg,
        "dac_writes": dac,
        "key_ons": {str(k): v for k, v in sorted(keys.items())},
        # Frame 1 is the start of the song, so a stored loop frame of 1 is 0 s.
        "loop_seconds": str(Fraction(loop_frame - 1, FRAME_RATE)) if loop_frame else None,
    }
