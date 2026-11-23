"""Apple ProRes elementary streams (SMPTE RDD 36): frame headers, pictures and slice tables."""
import struct

# SMPTE RDD 36 frame header code tables.
# frame_rate_code: 0 is unknown, the NTSC rates are the fractional ones.
RATES = {1: "23.976", 2: "24", 3: "25", 4: "29.97", 5: "30", 6: "50", 7: "59.94", 8: "60"}
ASPECT = {1: "1:1", 2: "4:3", 3: "16:9"}
# chroma_format is a 2-bit code in the frame header; 2 = 4:2:2, 3 = 4:4:4.
CHROMA = {2: "4:2:2", 3: "4:4:4"}
# interlace_mode: 0 = progressive, 1 = top field first, 2 = bottom field first.
SCAN = {0: "progressive", 1: "interlaced_tff", 2: "interlaced_bff"}
# alpha_info: 0/absent = no alpha, 1 = 8 bit, 2 = 16 bit.
ALPHA = {1: 8, 2: 16}

FRAME_MAGIC = b"icpf"
FRAME_HEADER_BYTES = 8  # uint32 frame size plus the "icpf" container identifier


class ProresError(ValueError):
    pass


def _frame(data, pos, size):
    """Parse one complete frame; ``pos .. pos + size`` is guaranteed to lie inside ``data``.

    The 32-bit frame size counts the whole container, including the size field itself,
    so the next frame starts exactly at ``pos + size``.
    """
    end = pos + size
    h = pos + FRAME_HEADER_BYTES
    hsize, version = struct.unpack_from(">HH", data, h)
    creator = data[h + 4:h + 8].decode("latin-1")
    width, height = struct.unpack_from(">HH", data, h + 8)
    flags1, flags2, cp, tc, mc, alpha_byte, _reserved, qflags = struct.unpack_from(">8B", data, h + 12)

    chroma_code = flags1 >> 6                 # bits 7-6
    interlace = (flags1 >> 2) & 0x03          # bits 3-2
    info = {"version": version, "creator": creator, "width": width, "height": height,
            "chroma": CHROMA.get(chroma_code), "scan": SCAN.get(interlace),
            "aspect": ASPECT.get(flags2 >> 4), "frame_rate": RATES.get(flags2 & 0x0F),
            "color": [cp, tc, mc], "alpha_bits": ALPHA.get(alpha_byte & 0x0F),
            "custom_matrices": bool(qflags & 3)}

    # Interlaced frames carry two pictures, one per field; progressive frames one.
    pictures = 2 if interlace in (1, 2) else 1
    # Pictures begin after the whole frame header, which grows when custom
    # quantisation matrices are present.
    p = h + hsize
    slices, ok = 0, p <= end
    for _ in range(pictures):
        if not ok or p + 7 > end:
            ok = False
            break
        phsize = data[p] >> 3
        psize, nslices = struct.unpack_from(">IH", data, p + 1)
        table_bytes = 2 * nslices
        # Picture header, slice size table and slice data must stay in the frame,
        # and the slice table entries must account for every slice-data byte.
        if (phsize < 7 or psize < phsize + table_bytes or p + psize > end
                or sum(struct.unpack_from(">%dH" % nslices, data, p + phsize))
                != psize - phsize - table_bytes):
            ok = False
        slices += nslices
        p += psize

    # The pictures (and nothing else) fill the frame after the container and header.
    if p != end:
        ok = False
    info.update(pictures=pictures, slices=slices, consistent=ok)
    return info


def parse(data):
    pos, n = 0, len(data)
    frames, sizes, first, bad = 0, [], None, 0
    truncated = False
    while pos < n:
        if n - pos < 8:  # a frame header cut off at end of stream
            truncated = True
            break
        size, magic = struct.unpack_from(">I4s", data, pos)
        if magic != FRAME_MAGIC:
            raise ProresError("no icpf frame at %d" % pos)
        if size < 8 or pos + size > n:  # declared frame runs past end of stream
            truncated = True
            break
        info = _frame(data, pos, size)
        first = first or info
        frames += 1
        sizes.append(size)
        bad += not info["consistent"]
        pos += size
    if first is None:
        raise ProresError("no complete ProRes frame")
    out = {k: v for k, v in first.items() if k != "consistent"}
    out.update(frames=frames, min_frame_bytes=min(sizes), max_frame_bytes=max(sizes),
               inconsistent_frames=bad, truncated=truncated or pos != n)
    return out
