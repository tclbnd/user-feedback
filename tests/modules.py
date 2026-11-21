"""Builders for small synthetic ScreamTracker 3 modules used by the tests."""
import struct


def ins(name="smp", frames=200, bits=8, stereo=False, loop=None, volume=48, c2spd=8363):
    """loop: (begin, end) in frames, or None."""
    return {"name": name, "frames": frames, "bits": bits, "stereo": stereo, "loop": loop, "volume": volume,
            "c2spd": c2spd, "type": 1}


def packed_pattern(grid):
    """grid: {(row, ch): (note, ins, vol, cmd, info)} with None for absent fields."""
    body = b""
    for r in range(64):
        for (rr, ch), (note, i, vol, cmd, info) in sorted(grid.items()):
            if rr != r:
                continue
            what = ch | (32 if note is not None else 0) | (64 if vol is not None else 0) | (128 if cmd is not None else 0)
            body += bytes([what])
            if note is not None:
                body += bytes([note, i or 0])
            if vol is not None:
                body += bytes([vol])
            if cmd is not None:
                body += bytes([cmd, info])
        body += b"\0"
    return struct.pack("<H", len(body) + 2) + body


def _align(b):
    return b + bytes(-len(b) % 16)


def module(title="song", channels=(0, 1, 2, 3), orders=(0, 255), instruments=(), patterns=(None,), speed=6, tempo=125):
    ffi, cwt, stereo, gvol, empty_ptr = 2, 0x1320, True, 64, ()
    chan = bytes(channels[i] if i < len(channels) else 255 for i in range(32))
    head = title.encode("latin-1").ljust(28, b"\0") + b"\x1a\x10\0\0"
    head += struct.pack("<6H", len(orders), len(instruments), len(patterns), 0, cwt, ffi) + b"SCRM"
    head += bytes([gvol, speed, tempo, 0xB0 if stereo else 0x30, 0, 0]) + bytes(8) + b"\0\0" + chan
    head += bytes(orders)
    n_ptr = 2 * (len(instruments) + len(patterns))
    base = len(_align(head + bytes(n_ptr)))
    blocks, ins_ptr, pat_ptr = b"", [], []
    # instrument headers
    hdrs = []
    for i in instruments:
        ins_ptr.append((base + len(blocks)) // 16)
        blocks += bytes(80)
        hdrs.append(len(blocks) - 80)
    for k, g in enumerate(patterns):
        if k in empty_ptr:
            pat_ptr.append(0)
            continue
        pat_ptr.append((base + len(blocks)) // 16)
        blocks = _align(blocks + packed_pattern(g or {}))
    blocks = bytearray(blocks)
    for i, off in zip(instruments, hdrs):
        width = (2 if i["bits"] == 16 else 1) * (2 if i["stereo"] else 1)
        data_para = (base + len(blocks)) // 16
        lb, le = i["loop"] or (0, 0)
        flags = (1 if i["loop"] else 0) | (2 if i["stereo"] else 0) | (4 if i["bits"] == 16 else 0)
        h = bytes([i["type"]]) + b"sample.raw".ljust(12, b"\0") + bytes([data_para >> 16]) + struct.pack("<H", data_para & 0xFFFF)
        h += struct.pack("<III", i["frames"], lb, le) + bytes([i["volume"], 0, 0, flags]) + struct.pack("<I", i["c2spd"])
        h += bytes(12) + i["name"].encode("latin-1").ljust(28, b"\0") + (b"SCRS" if i["type"] == 1 else b"SCRI")
        blocks[off:off + 80] = h
        blocks += _align(bytes(((k * 5) & 0x7F) for k in range(i["frames"] * width)))
    ptrs = struct.pack("<%dH" % len(ins_ptr), *ins_ptr) + struct.pack("<%dH" % len(pat_ptr), *pat_ptr)
    return _align(head + ptrs) + bytes(blocks)


def n(octave, semi):
    return (octave << 4) | semi
