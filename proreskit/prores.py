"""Apple ProRes elementary streams (SMPTE RDD 36): frame headers, pictures and slice tables."""
import struct

# RDD 36 Table 4.4: frame_rate_code (0 means unknown)
RATES = {1: "23.976", 2: "24", 3: "25", 4: "29.97", 5: "30",
         6: "50", 7: "59.94", 8: "60"}
# RDD 36 Table 4.3: aspect_ratio_info (0 means unknown)
ASPECT = {1: "1:1", 2: "4:3", 3: "16:9"}
# frame_flags_1 bits 7-6: chroma_format
CHROMA = {2: "4:2:2", 3: "4:4:4"}
# frame_flags_1 bits 3-2: interlaced_mode
SCAN = {0: "progressive", 1: "interlaced_tff", 2: "interlaced_bff"}
# alpha_info
ALPHA = {0: None, 1: 8, 2: 16}

MAGIC = b"icpf"
# picture header byte 0, bit 2: slice sizes are 32-bit words instead of 16-bit
PIC_WIDE_SLICES = 0x04


class ProresError(ValueError):
    pass


def _frame(data, pos):
    """Parse one frame container. Return (info, frame_size); (None, size) when the
    declared frame runs past the end of the stream."""
    if pos + 8 > len(data):
        return None, 0
    size, magic = struct.unpack_from(">I4s", data, pos)
    if magic != MAGIC:
        raise ProresError("no icpf frame at %d" % pos)
    if size < 8 or pos + size > len(data):
        return None, size
    frame_end = pos + size
    h = pos + 8
    hsize, version = struct.unpack_from(">HH", data, h)
    creator = data[h + 4:h + 8].decode("latin-1")
    width, height = struct.unpack_from(">HH", data, h + 8)
    flags1, flags2, cp, tc, mc, alpha, _reserved, qm_flags = struct.unpack_from(">8B", data, h + 12)
    scan = (flags1 >> 2) & 3
    info = {"version": version, "creator": creator, "width": width, "height": height,
            "chroma": CHROMA.get((flags1 >> 6) & 3),
            "scan": SCAN.get(scan),
            "aspect": ASPECT.get((flags2 >> 4) & 0x0F),
            "frame_rate": RATES.get(flags2 & 0x0F),
            "color": [cp, tc, mc],
            "alpha_bits": ALPHA.get(alpha),
            "custom_matrices": bool(qm_flags & 3)}
    # Progressive frames hold one picture; interlaced frames hold two, one per field.
    pictures = 2 if scan else 1
    p = h + hsize
    slices = 0
    consistent = True
    for _ in range(pictures):
        if p + 8 > frame_end:
            consistent = False
            break
        first_byte = data[p]
        phsize = first_byte >> 3
        wide_slices = bool(first_byte & PIC_WIDE_SLICES)
        psize, nslices = struct.unpack_from(">IH", data, p + 1)
        entry = 4 if wide_slices else 2
        table_pos = p + phsize
        if psize < phsize or table_pos + nslices * entry > frame_end:
            consistent = False
            break
        if wide_slices:
            table = struct.unpack_from(">%dI" % nslices, data, table_pos)
        else:
            table = struct.unpack_from(">%dH" % nslices, data, table_pos)
        slices += nslices
        # Header + slice table + slice payloads must add up to the picture size.
        if phsize + nslices * entry + sum(table) != psize:
            consistent = False
        p += psize
    # All pictures together must fill exactly the frame container.
    if p != frame_end:
        consistent = False
    info.update(pictures=pictures, slices=slices, consistent=consistent)
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
    if pos != len(data):
        truncated = True
    out = {k: v for k, v in first.items() if k != "consistent"}
    out.update(frames=frames, min_frame_bytes=min(sizes), max_frame_bytes=max(sizes),
               inconsistent_frames=bad, truncated=truncated)
    return out
