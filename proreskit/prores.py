"""Apple ProRes elementary streams (SMPTE RDD 36): frame headers, pictures and slice tables."""
import struct

RATES = {1: "24000/1001", 2: "24", 3: "25", 4: "30000/1001", 5: "30", 6: "50",
         7: "60000/1001", 8: "60", 9: "100", 10: "120000/1001", 11: "120"}
ASPECT = {1: "1:1", 2: "4:3", 3: "16:9"}
CHROMA = {2: "4:2:2", 3: "4:4:4"}
SCAN = {0: "progressive", 1: "interlaced_tff", 2: "interlaced_bff"}
ALPHA = {0: None, 1: 8, 2: 16}


class ProresError(ValueError):
    pass


def _frame(data, pos):
    size, magic = struct.unpack_from(">I4s", data, pos)
    if magic != b"icpf":
        raise ProresError("no icpf frame at %d" % pos)
    if size < 8 + 20:
        raise ProresError("invalid frame size %d at %d" % (size, pos))
    if pos + size > len(data):
        return None, size
    h = pos + 8
    hsize, version = struct.unpack_from(">HH", data, h)
    creator = data[h + 4:h + 8].decode("latin-1")
    width, height = struct.unpack_from(">HH", data, h + 8)
    b1, b2, cp, tc, mc, b3, _, flags = struct.unpack_from(">8B", data, h + 12)
    chroma, scan = CHROMA.get(b1 >> 6), SCAN.get((b1 >> 2) & 3)
    info = {"version": version, "creator": creator, "width": width, "height": height,
            "chroma": chroma, "scan": scan, "aspect": ASPECT.get(b2 >> 4),
            "frame_rate": RATES.get(b2 & 0x0F), "color": [cp, tc, mc], "alpha_bits": ALPHA.get(b3 & 0x0F),
            "custom_matrices": bool(flags & 3)}
    p = h + hsize
    pictures = 1 if scan == "progressive" else 2
    slices, ok = 0, True
    for _ in range(pictures):
        phsize = data[p] >> 3
        psize, nslices = struct.unpack_from(">IH", data, p + 1)
        table = struct.unpack_from(">%dH" % nslices, data, p + phsize)
        if psize != phsize + 2 * nslices + sum(table):
            ok = False
        slices += nslices
        p += psize
    if p != pos + size:
        ok = False
    info.update(pictures=pictures, slices=slices, consistent=ok)
    return info, size


def parse(data):
    pos, frames, sizes, first, bad = 0, 0, [], None, 0
    truncated = False
    while pos + 8 <= len(data):
        info, size = _frame(data, pos)
        if info is None:
            truncated = True
            break
        first = first or info
        frames += 1
        sizes.append(size)
        bad += not info["consistent"]
        pos += size
    if first is None:
        raise ProresError("no complete ProRes frame")
    out = {k: v for k, v in first.items() if k not in ("consistent",)}
    out.update(frames=frames, min_frame_bytes=min(sizes), max_frame_bytes=max(sizes), inconsistent_frames=bad,
               truncated=truncated or pos != len(data))
    return out
