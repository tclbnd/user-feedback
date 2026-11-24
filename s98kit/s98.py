"""S98 sound logs (PC-98 / FM chips): header v1-v3, device list, sync commands, loop point, [S98] tags."""
import struct
from fractions import Fraction

DEVICES = {0: "none", 1: "ym2149", 2: "ym2203", 3: "ym2612", 4: "ym2608", 5: "ym2151", 6: "ym2413", 7: "ym3526",
           8: "ym3812", 9: "ymf262", 15: "ay-3-8910", 16: "sn76489"}

# Header (0x20 bytes): magic "S98", version byte, six little-endian dwords.
HEADER_SIZE = 0x20
# Versions 1 and 2 carry no device table; the spec assigns two YM2203 chips.
DEFAULT_CLOCK = 3993600
DEFAULT_DEVICE_TYPE = 2                                  # YM2203
# A zero timer numerator/denominator means the 10/1000 s default tick.
DEFAULT_NUMERATOR = 10
DEFAULT_DENOMINATOR = 1000

CMD_END = 0xFD
CMD_SYNC_N = 0xFE
CMD_SYNC_1 = 0xFF


class S98Error(ValueError):
    pass


def _device(device_type):
    return {"type": DEVICES.get(device_type, "type_%d" % device_type),
            "clock": DEFAULT_CLOCK if device_type else 0, "pan": 0}


def _parse_devices(data, version):
    """Device table: stored by version 3, synthesized for versions 1/2."""
    if version != "3":
        return [_device(DEFAULT_DEVICE_TYPE), _device(DEFAULT_DEVICE_TYPE)]
    if len(data) <= HEADER_SIZE:
        raise S98Error("truncated v3 device count")
    count, = struct.unpack_from("<I", data, 0x1C)
    # A normal table ends before the dump; a header-only log may end on (or just
    # inside) the last entry, so only an entry with no bytes to read is fatal.
    if count and HEADER_SIZE + 4 + 16 * (count - 1) >= len(data):
        raise S98Error("truncated v3 device table")
    devices = []
    for i in range(count):
        device_type, clock, pan, _reserved = struct.unpack_from("<4I", data, HEADER_SIZE + 16 * i)
        devices.append({"type": DEVICES.get(device_type, "type_%d" % device_type),
                        "clock": clock, "pan": pan})
    return devices


def _parse_tags(data, tag_off):
    """Tag block: an [S98] key=value section, or old-style free text (title)."""
    if not tag_off or tag_off >= len(data):
        return {}
    raw = data[tag_off:]
    nul = raw.find(b"\x00")
    if nul != -1:
        raw = raw[:nul]
    if not raw:
        return {}
    text = None
    for encoding in ("utf-8-sig", "utf-8", "shift_jis", "cp932", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    # Writers may put a BOM at the start of the block or right after "[S98]".
    text = text.replace(chr(0xFEFF), "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {}
    # An [S98] section may be its own line or glued to the first key by a BOM.
    if lines[0].lower().startswith("[s98]"):
        lines[0] = lines[0][len("[S98]"):].strip()
        body = [line for line in lines if line]
        tags = {}
        for line in body:
            if "=" in line:
                key, value = line.split("=", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            tags[key.strip().lower()] = value.strip()
        return tags
    return {"title": text.strip()}


def parse(data):
    if data[:3] != b"S98" or len(data) < HEADER_SIZE:
        raise S98Error("not an S98 log")
    version = chr(data[3])
    if version not in ("1", "2", "3"):
        raise S98Error("unknown S98 version %r" % version)
    num, den, _timer2, tag_off, dump_off, loop_off = struct.unpack_from("<6I", data, 4)
    # One sync lasts numerator/denominator seconds; zero fields take the default.
    num = num or DEFAULT_NUMERATOR
    den = den or DEFAULT_DENOMINATOR
    tick = Fraction(num, den)

    devices = _parse_devices(data, version)
    writes = [0] * len(devices)

    start = dump_off or HEADER_SIZE
    # The dump runs up to the tag block when one follows it.
    end = tag_off if tag_off and tag_off > start else len(data)
    p = start
    syncs = 0
    loop_syncs = None
    ended = False
    while p < end:
        at_loop = bool(loop_off) and p == loop_off and loop_syncs is None
        c = data[p]
        p += 1
        if at_loop:
            loop_syncs = syncs
        if c == CMD_SYNC_1:                               # wait one tick
            syncs += 1
        elif c == CMD_SYNC_N:                             # wait n+2 ticks
            shift = 0
            value = 0
            while True:
                if p >= end:
                    raise S98Error("truncated sync count at %d" % p)
                b = data[p]
                p += 1
                value |= (b & 0x7F) << shift
                if not b & 0x80:
                    break
                shift += 7
            syncs += value + 2
        elif c == CMD_END:                                # end (also marks a loop jump)
            ended = True
            break
        elif c < 0x80:                                    # register write: low nibble = device
            device = c & 0x0F
            if device >= len(devices):
                raise S98Error("write to device %d at %d" % (device, p - 1))
            if p + 2 > end:
                raise S98Error("truncated register write at %d" % (p - 1))
            writes[device] += 1
            p += 2
        else:
            raise S98Error("unknown command 0x%02X at %d" % (c, p - 1))

    tags = _parse_tags(data, tag_off)
    return {"version": version, "tick": str(tick), "devices": devices, "writes": writes, "syncs": syncs,
            "duration": str(syncs * tick),
            "loop_seconds": str(loop_syncs * tick) if loop_syncs is not None else None,
            "end_marker": ended, "tags": tags}
