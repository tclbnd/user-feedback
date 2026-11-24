"""Sega Genesis / Mega Drive register logs (.gym): GYMX header, zlib packing, YM2612 and PSG writes at 60 Hz."""
import struct
import zlib
from fractions import Fraction

MAGIC = b"GYMX"
HEADER_SIZE = 0x1AC          # magic + five 32-byte strings + 256-byte comment + two 32-bit fields
LOOP_OFFSET = 0x1A4          # loop frame stored by Gens, 0 = no loop
STRINGS = (("song", 0x04, 32), ("game", 0x24, 32), ("publisher", 0x44, 32),
           ("emulator", 0x64, 32), ("dumper", 0x84, 32), ("comment", 0xA4, 256))
FRAMES_PER_SECOND = 60       # each command 0x00 waits one NTSC frame

KEY_ON_REG = 0x28            # YM2612 key on/off, low three bits select channel 1-6
OPERATOR_MASK = 0xF0         # bits 7-4 of 0x28: operators 1-4 key on
DAC_DATA_REG = 0x2A          # YM2612 DAC data, port 0 only
DAC_ENABLE_REG = 0xB6        # YM2612 DAC enable, bit 7
DAC_ENABLE_BIT = 0x80
FM_CHANNELS = 6
PSG_CHANNELS = 4             # SN76489 tone 0-2 and noise 3
PSG_LATCH = 0x80             # SN76489 byte bit 7 set: latch byte
PSG_VOLUME = 0x10            # latched attenuator register
PSG_ATTENUATION_MASK = 0x0F  # 0 = loudest ... 15 = silent
PSG_SILENT = 0x0F


class GymError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def read_header(data):
    """Return the GYMX header of *data*, or None when the file has no GYMX header."""
    if data[:4] != MAGIC:
        return None
    if len(data) < HEADER_SIZE:
        raise GymError("truncated GYMX header")
    head = {}
    for name, offset, size in STRINGS:
        head[name] = text(data[offset:offset + size])
    head["loop_frame"] = struct.unpack_from("<I", data, LOOP_OFFSET)[0] or None
    head["packed"] = False
    return head


def unpack(body):
    """Split a GYM payload into its command stream and whether it was zlib packed."""
    if body[:1] == b"\x78":          # zlib header; 0x78 is not a valid GYM command
        try:
            return zlib.decompress(body), True
        except zlib.error as exc:
            raise GymError("corrupt packed log: %s" % exc)
    return body, False


def parse(data):
    head = read_header(data)
    if head is None:
        body, packed = data, False
    else:
        body, packed = unpack(data[HEADER_SIZE:])
        head["packed"] = packed

    frames = port0 = port1 = psg = dac = 0
    keys = dict.fromkeys(range(1, FM_CHANNELS + 1), 0)
    volume_writes = dict.fromkeys(range(PSG_CHANNELS), 0)
    volumes = dict.fromkeys(range(PSG_CHANNELS), PSG_SILENT)
    dac_enabled = False

    p, n = 0, len(body)
    while p < n:
        op = body[p]
        if op == 0x00:                       # wait one frame
            frames += 1
            p += 1
        elif op in (0x01, 0x02):             # YM2612 port 0 / port 1: register, value
            if p + 3 > n:
                break
            reg, val = body[p + 1], body[p + 2]
            if op == 0x01:
                port0 += 1
                if reg == DAC_DATA_REG:
                    dac += 1
                elif reg == DAC_ENABLE_REG:
                    dac_enabled = bool(val & DAC_ENABLE_BIT)
                if reg == KEY_ON_REG:
                    channel = (val & 0x07) + 1
                    if val & OPERATOR_MASK and channel <= FM_CHANNELS:
                        keys[channel] += 1
            else:
                port1 += 1
            p += 3
        elif op == 0x03:                     # PSG: one latched SN76489 byte
            if p + 2 > n:
                break
            byte = body[p + 1]
            psg += 1
            if byte & PSG_LATCH and byte & PSG_VOLUME:
                channel = (byte >> 5) & 0x03
                volume_writes[channel] += 1
                volumes[channel] = byte & PSG_ATTENUATION_MASK
            p += 2
        else:
            raise GymError("unknown command 0x%02X at %d" % (op, p))

    loop = head["loop_frame"] if head else None
    out = {"header": head, "frames": frames,
           "duration": str(Fraction(frames, FRAMES_PER_SECOND)),
           "ym_port0_writes": port0, "ym_port1_writes": port1, "psg_writes": psg,
           "dac_writes": dac, "dac_enabled": dac_enabled,
           "psg_volume_writes": {str(k): v for k, v in volume_writes.items()},
           "psg_volumes": {str(k): v for k, v in volumes.items()},
           "key_ons": {str(k): v for k, v in keys.items()},
           # frame 1 is the start of the song, so the stored loop frame is one-based
           "loop_seconds": str(Fraction(max(loop - 1, 0), FRAMES_PER_SECOND)) if loop else None}
    return out
