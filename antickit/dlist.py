"""Atari 8-bit memory snapshots (.xlm): walk the ANTIC display list."""
import struct

LINES = {2: 8}
WIDTH = {2: 40}
LIMIT = 2000


class DisplayListError(ValueError):
    pass


def parse(data):
    if len(data) != 9 + 65536 or data[:6] != b"XLMEM\x00":
        raise DisplayListError("not a memory snapshot")
    pc, dmactl = struct.unpack_from("<HB", data, 6)
    mem = data[9:]
    if dmactl & 3 == 0:
        raise DisplayListError("playfield DMA is off")

    def fetch():
        nonlocal pc
        b = mem[pc]
        pc = (pc + 1) & 0xFFFF
        return b

    scan = mode_lines = blank_lines = dli = lms_count = jumps = screen = 0
    msc = 0
    modes = {}
    ranges = []
    for _ in range(LIMIT):
        op = fetch()
        low = op & 15
        if low == 0:
            n = (op >> 4 & 7) + 1
            scan += n
            blank_lines += n
            continue
        if low == 1:
            fetch(), fetch()
            jumps += 1
            break
        if low not in LINES:
            raise DisplayListError("unsupported mode %X" % low)
        if op & 0x40:
            msc = fetch() | fetch() << 8
            lms_count += 1
        n = WIDTH[low]
        start = msc
        msc = (msc + n) & 0xFFFF
        if ranges and ranges[-1][1] == start:
            ranges[-1][1] = msc
        else:
            ranges.append([start, msc])
        screen += n
        mode_lines += 1
        scan += LINES[low]
        modes[low] = modes.get(low, 0) + 1
    else:
        raise DisplayListError("display list never reaches a JVB")
    return {"scanlines": scan, "mode_lines": mode_lines, "blank_lines": blank_lines, "dli": dli,
            "lms": lms_count, "jumps": jumps, "screen_bytes": screen,
            "modes": {"%X" % m: modes[m] for m in sorted(modes)},
            "fetch": ["%04x-%04x" % (a, b) for a, b in ranges]}
