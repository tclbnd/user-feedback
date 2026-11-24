"""S98 sound logs (PC-98 / FM chips): header v1-v3, device list, sync commands, loop point, [S98] tags.

Implements the S98 format specification, version 3, while remaining
compatible with versions 1 and 2:

* header timer (seconds per sync), including the v1 / 2.5 compatibility
  divisor and the 10 / 1000 defaults;
* device table, and the three implicit devices (YM2608, YM2203, YM2612)
  used when no device table exists (v1/v2, or a zero-count v3 table);
* command stream: write commands carry the device index in bits 1..6 and
  the port in bit 0; 0xFF is one sync, 0xFE counts two or more syncs as
  7-bit groups, least significant group first, plus 2; 0xFD ends the log;
* loop point, measured in syncs from the start of the dump;
* the tag block, either an [S98] key/value block (UTF-8 or Shift_JIS) or
  an old-style plain title.
"""
import struct
from fractions import Fraction

DEVICES = {0: "none", 1: "ym2149", 2: "ym2203", 3: "ym2612", 4: "ym2608", 5: "ym2151", 6: "ym2413", 7: "ym3526",
           8: "ym3812", 9: "ymf262", 15: "ay-3-8910", 16: "sn76489"}

# Implicit device table used by v1/v2 logs and by v3 logs whose device
# count is 0: (type id, clock in Hz, pan).
DEFAULT_DEVICES = [(4, 7987200, 0), (2, 3993600, 0), (3, 7670454, 0)]

DEFAULT_TIMER_COUNT = 10
DEFAULT_TIMER_RATE = 1000
MAGIC = b"S98"
HEADER_SIZE = 0x20
DEVICE_ENTRY_SIZE = 16


class S98Error(ValueError):
    pass


def _decode_tag_bytes(raw):
    """Decode a tag block: UTF-8 first, then Shift_JIS, without BOMs."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("shift_jis", errors="replace")
    return text.replace("﻿", "")


def _parse_tags(raw):
    """Parse the NUL-terminated tag block into a lower-case-key dict.

    An [S98] block holds ``key=value`` lines (one entry per line). Anything
    else is an old-style tag whose whole text is the title.
    """
    if not raw:
        return {}
    text = _decode_tag_bytes(raw).strip()
    if not text:
        return {}
    if not text.startswith("[S98]"):
        return {"title": text}
    tags = {}
    body = text[len("[S98]"):]
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        tags[key.strip().lower()] = value.strip()
    return tags


def parse(data):
    n = len(data)
    if data[:3] != MAGIC or n < HEADER_SIZE:
        raise S98Error("not an S98 log")
    version = chr(data[3])
    if version not in ("1", "2", "3"):
        raise S98Error("unknown S98 version %r" % data[3])

    if version == "1":
        # v1 has no timer fields: its fixed 4 ms sync is reproduced with
        # TIMER_COMPATI = 1 (the /2.5 compatibility divisor).
        timer_count, timer_rate, timer_compat = DEFAULT_TIMER_COUNT, DEFAULT_TIMER_RATE, 1
        tag_off, dump_off, loop_off = struct.unpack_from("<3I", data, 0x10)
        device_count = 0
    else:
        timer_count, timer_rate, timer_compat, tag_off, dump_off, loop_off = struct.unpack_from("<6I", data, 4)
        device_count = struct.unpack_from("<I", data, 0x1C)[0] if version == "3" else 0
        timer_count = timer_count or DEFAULT_TIMER_COUNT
        timer_rate = timer_rate or DEFAULT_TIMER_RATE

    # Seconds per sync: TIMER_COUNT / TIMER_RATE, divided by 2.5 when the
    # compatibility flag is set (v1 timing).
    tick = Fraction(timer_count, timer_rate)
    if timer_compat == 1:
        tick /= Fraction(5, 2)

    devices = []
    table_end = HEADER_SIZE
    if version == "3" and device_count:
        table_end = HEADER_SIZE + DEVICE_ENTRY_SIZE * device_count
        if table_end > n:
            raise S98Error("device table runs past end of file")
        for i in range(device_count):
            t, clock, pan, _ = struct.unpack_from("<4I", data, HEADER_SIZE + DEVICE_ENTRY_SIZE * i)
            devices.append({"type": DEVICES.get(t, "type_%d" % t), "clock": clock, "pan": pan})
    else:
        # No device table: the implicit PC-98 / Mega Drive devices.
        for t, clock, pan in DEFAULT_DEVICES:
            devices.append({"type": DEVICES[t], "clock": clock, "pan": pan})

    if dump_off == 0:
        dump_off = table_end if (version == "3" and device_count) else HEADER_SIZE
    if dump_off > n:
        raise S98Error("dump offset %d runs past end of file" % dump_off)
    if loop_off and loop_off < dump_off:
        raise S98Error("loop offset %d precedes the dump" % loop_off)

    writes = [0] * len(devices)
    p = dump_off
    syncs = 0
    loop_syncs = None
    ended = False
    while p < n:
        c = data[p]
        if c == 0xFF:                                   # one sync
            syncs += 1
            p += 1
        elif c == 0xFE:                                 # two or more syncs
            p += 1
            v = 0
            shift = 0
            while True:
                if p >= n:
                    raise S98Error("truncated 0xFE sync count at %d" % p)
                b = data[p]
                p += 1
                v |= (b & 0x7F) << shift                # 7-bit groups, least significant first
                shift += 7
                if not b & 0x80:
                    break
            syncs += v + 2                              # encoded value plus 2
        elif c == 0xFD:                                 # end of log (loop target when looping)
            ended = True
            break
        elif c < 0x80:                                  # register write
            dev = (c >> 1) & 0x3F                       # bits 1..6: device index
            # bit 0: port (0 = address/data port 0, 1 = port 1)
            if dev >= len(devices):
                raise S98Error("write to device %d at %d" % (dev, p))
            if p + 3 > n:
                raise S98Error("truncated write command at %d" % p)
            writes[dev] += 1
            p += 3
        else:
            raise S98Error("unknown command 0x%02X at %d" % (c, p))
        if loop_syncs is None and loop_off and p == loop_off:
            loop_syncs = syncs

    tags = {}
    if tag_off:
        if tag_off >= n:
            raise S98Error("tag offset %d runs past end of file" % tag_off)
        end = data.find(b"\x00", tag_off)
        raw = data[tag_off:] if end < 0 else data[tag_off:end]
        tags = _parse_tags(raw)

    return {"version": version, "tick": str(tick), "devices": devices, "writes": writes, "syncs": syncs,
            "duration": str(syncs * tick), "loop_seconds": str(loop_syncs * tick) if loop_syncs is not None else None,
            "end_marker": ended, "tags": tags}
