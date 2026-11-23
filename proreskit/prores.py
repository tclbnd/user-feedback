"""Apple ProRes elementary streams (SMPTE RDD 36): frame headers, pictures and slice tables."""
import struct

# Frame rate code, frame header byte 13 bits 3..0 (RDD 36 table 4).
RATES = {1: "24000/1001", 2: "24", 3: "25", 4: "30000/1001", 5: "30",
         6: "50", 7: "60000/1001", 8: "60", 9: "100", 10: "120000/1001",
         11: "120"}
ASPECT = {1: "1:1", 2: "4:3", 3: "16:9"}
CHROMA = {2: "4:2:2", 3: "4:4:4"}
SCAN = {0: "progressive", 1: "interlaced_tff", 2: "interlaced_bff"}
ALPHA = {1: 8, 2: 16}

# ITU-T H.273 code points.  Code 2 (unspecified), reserved and unknown codes
# are left out of the tables and reported as null.
PRIMARIES = {1: "bt709", 4: "bt470m", 5: "bt601_625", 6: "bt601_525",
             7: "smpte240m", 8: "film", 9: "bt2020", 10: "xyz", 11: "dci_p3",
             12: "p3_d65", 22: "ebu3213"}
TRANSFER = {1: "bt709", 4: "bt470m", 5: "bt470bg", 6: "bt601", 7: "smpte240m",
            8: "linear", 13: "srgb", 14: "bt2020_10", 15: "bt2020_12",
            16: "pq", 17: "st428", 18: "hlg"}
MATRIX = {0: "rgb", 1: "bt709", 4: "fcc", 5: "bt601", 6: "bt601",
          7: "smpte240m", 9: "bt2020_ncl", 10: "bt2020_cl", 14: "ictcp"}

FRAME_HEADER_SIZE = 20
PICTURE_HEADER_SIZE = 7


class ProresError(ValueError):
    pass


def _frame(data, pos):
    """Parse the frame at *pos*.

    Returns ``(info, size)``, or ``(None, size)`` when the frame is cut short by
    the end of the stream.  *size* counts the four size bytes and the ``icpf``
    identifier, so consecutive frames start exactly *size* bytes apart.
    """
    size, magic = struct.unpack_from(">I4s", data, pos)
    if magic != b"icpf":
        raise ProresError("no icpf frame at %d" % pos)
    if size < 8:
        raise ProresError("invalid frame size %d at %d" % (size, pos))
    if pos + size > len(data):
        return None, size

    end = pos + size
    h = pos + 8
    info = {"version": None, "creator": None, "width": None, "height": None,
            "chroma": None, "scan": None, "aspect": None, "frame_rate": None,
            "color": None, "color_names": None, "alpha_bits": None,
            "custom_matrices": None, "pictures": 0, "slices": 0,
            "consistent": False}
    if h + FRAME_HEADER_SIZE > end:
        return info, size

    hsize, version = struct.unpack_from(">HH", data, h)
    creator = data[h + 4:h + 8].decode("latin-1")
    width, height = struct.unpack_from(">HH", data, h + 8)
    b1, b2, cp, tc, mc, alpha, _reserved, flags = struct.unpack_from(">8B", data, h + 12)

    # Byte 12: chroma format (2 bits), reserved (2), interlaced mode (2),
    # reserved (2).  Byte 13: aspect ratio (4), frame rate code (4).  The
    # interlaced mode is 0 for progressive, 1 for TFF and 2 for BFF.
    chroma_code = (b1 >> 6) & 3
    scan_code = (b1 >> 2) & 3
    info.update(
        version=version,
        creator=creator,
        width=width,
        height=height,
        chroma=CHROMA.get(chroma_code),
        scan=SCAN.get(scan_code),
        aspect=ASPECT.get(b2 >> 4),
        frame_rate=RATES.get(b2 & 0x0F),
        color=[cp, tc, mc],
        color_names={"primaries": PRIMARIES.get(cp),
                     "transfer": TRANSFER.get(tc),
                     "matrix": MATRIX.get(mc)},
        alpha_bits=ALPHA.get(alpha & 0x0F),
        custom_matrices=bool(flags & 3),
    )

    # Pictures follow the frame header, whose length also covers custom
    # quantisation matrices and any other header extension.  An interlaced
    # frame carries one picture per field, a progressive frame one, so the
    # pictures are read until the frame is used up.
    if hsize < FRAME_HEADER_SIZE or h + hsize > end:
        return dict(info, consistent=False), size

    p = h + hsize
    pictures = slices = 0
    consistent = True
    while p < end:
        if p + PICTURE_HEADER_SIZE > end:
            consistent = False
            break
        phsize = data[p] >> 3
        psize, nslices = struct.unpack_from(">IH", data, p + 1)
        table_end = p + phsize + 2 * nslices
        if phsize < PICTURE_HEADER_SIZE or table_end > end \
                or psize < phsize + 2 * nslices or p + psize > end:
            consistent = False
            break
        table = struct.unpack_from(">%dH" % nslices, data, p + phsize)
        # A picture is its header, its slice table and the slice data that the
        # table describes.
        if phsize + 2 * nslices + sum(table) != psize:
            consistent = False
        pictures += 1
        slices += nslices
        p += psize

    # Leftover bytes mean the picture sizes do not add up to the frame size.
    if p != end:
        consistent = False

    info.update(pictures=pictures, slices=slices, consistent=consistent)
    return info, size


def parse(data):
    pos, frames, sizes, first, bad = 0, 0, [], None, 0
    truncated = False
    while pos < len(data):
        if pos + 8 > len(data):
            truncated = True
            break
        info, size = _frame(data, pos)
        if info is None:
            truncated = True
            break
        if first is None:
            first = info
        frames += 1
        sizes.append(size)
        bad += not info["consistent"]
        pos += size
    if first is None:
        raise ProresError("no complete ProRes frame")
    out = {k: v for k, v in first.items() if k != "consistent"}
    out.update(frames=frames, min_frame_bytes=min(sizes),
               max_frame_bytes=max(sizes), inconsistent_frames=bad,
               truncated=truncated)
    return out
