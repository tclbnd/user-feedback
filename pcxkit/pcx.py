"""ZSoft PCX images: header, RLE scan lines, planes, EGA header palette, VGA 256-colour palette."""
import struct

# A run packet is introduced by a byte with its two high bits set (0xC0-0xFF);
# the low six bits hold the repeat count.  Anything below 0xC0 is literal pixel
# data, including 0x80-0xBF.
RLE_MARKER = 0xC0
RLE_COUNT_MASK = 0x3F


class PcxError(ValueError):
    pass


def decode(data, p, want):
    """Decode RLE data from `p` until `want` bytes are produced.

    Runs are not tied to planes or scan lines: every packet simply appends
    bytes to the flat pixel stream, so a run may cross either boundary.
    """
    out = bytearray()
    runs = 0
    while len(out) < want and p < len(data):
        b = data[p]
        p += 1
        if b >= RLE_MARKER:
            if p >= len(data):
                break
            out += bytes([data[p]]) * (b & RLE_COUNT_MASK)
            p += 1
            runs += 1
        else:
            out.append(b)
    return bytes(out[:want]), p, runs


def _ega_index(row, bpl, bits, planes):
    """Combine the bits of the first pixel from the planes into a palette index.

    Planes are stored low-order first: plane 0 supplies the least significant
    bits of the value (EGA plane 0 is blue, i.e. bit 0 of the colour index).
    """
    index = 0
    for k in range(planes):
        part = (row[k * bpl] >> (8 - bits)) & ((1 << bits) - 1)
        index |= part << (k * bits)
    return index


def parse(data):
    if len(data) < 128 or data[0] != 0x0A:
        raise PcxError("not a PCX file")
    version, encoding, bits = data[1], data[2], data[3]
    xmin, ymin, xmax, ymax, hdpi, vdpi = struct.unpack_from("<6H", data, 4)
    ega = data[16:64]
    planes = data[65]
    bpl, palinfo = struct.unpack_from("<HH", data, 66)
    w, h = xmax - xmin + 1, ymax - ymin + 1
    line = planes * bpl
    want = line * h
    if encoding == 1:
        pixels, body_end, runs = decode(data, 128, want)
    else:
        pixels, body_end, runs = data[128:128 + want], 128 + len(data[128:128 + want]), 0
    vga = None
    # The 256-colour palette is a 0x0C marker followed by 768 bytes at the very
    # end of the file.  It must also start at or after the pixel data so that a
    # 0x0C byte inside a short image is not taken for a palette.
    if bits == 8 and planes == 1 and len(data) >= 769 and data[-769] == 0x0C and body_end <= len(data) - 769:
        vga = data[-768:]
    if bits == 8 and planes == 1:
        kind = "vga256" if vga is not None else "none"
    elif bits == 8 and planes in (3, 4):
        kind = "truecolor"
    elif bits * planes == 4:
        kind = "ega16"
    elif bits * planes == 1:
        kind = "mono"
    else:
        kind = "other"
    corner = None
    if line > 0 and len(pixels) >= line:
        row = pixels[:line]
        rgb = None
        if kind == "vga256":
            i = row[0]
            rgb = vga[3 * i:3 * i + 3]
        elif kind == "truecolor":
            # Planes are stored whole-plane first: R, then G, then B.
            rgb = (row[0], row[bpl], row[2 * bpl])
        elif kind == "ega16":
            i = _ega_index(row, bpl, bits, planes)
            rgb = ega[3 * i:3 * i + 3]
        elif kind == "mono":
            rgb = (255, 255, 255) if row[0] & 0x80 else (0, 0, 0)
        if rgb is not None and len(rgb) == 3:
            corner = "#%02x%02x%02x" % tuple(rgb)
    return {"version": version, "rle": encoding == 1, "width": w, "height": h, "bits_per_plane": bits, "planes": planes,
            "bytes_per_line": bpl, "padding_bits": bpl * 8 // bits - w if bits else None, "dpi": [hdpi, vdpi],
            "palette": kind, "grayscale": palinfo == 2, "decoded_lines": len(pixels) // line if line > 0 else 0,
            "runs": runs, "complete": len(pixels) == want, "corner_pixel": corner}
