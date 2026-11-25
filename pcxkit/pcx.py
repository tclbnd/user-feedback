"""ZSoft PCX images: header, RLE scan lines, planes, EGA header palette, VGA 256-colour palette."""
import struct


class PcxError(ValueError):
    pass


def decode(data, p, line_bytes, lines):
    """Decode `lines` scan lines of `line_bytes` bytes; runs may cross plane and line boundaries.

    RLE rule per the ZSoft PCX format: a byte whose top two bits are both 1
    (0xC0..0xFF) is a run marker; its low six bits give the repeat count and
    the following byte is the value.  Any other byte, including 0x80..0xBF,
    is a literal pixel value.
    """
    out = bytearray()
    want = line_bytes * lines
    runs = 0
    while len(out) < want and p < len(data):
        b = data[p]
        p += 1
        if b & 0xC0 == 0xC0:
            count = b & 0x3F
            if p >= len(data):
                # Run marker without its value byte: the data is truncated.
                break
            value = data[p]
            p += 1
            if count:
                out += bytes([value]) * count
                runs += 1
        else:
            out.append(b)
    return bytes(out[:want]), p, runs


def _plane_pixel(row, bpl, bits, planes, x):
    """Colour index of pixel `x` in a decoded scan line (planes stored sequentially)."""
    index = 0
    for plane in range(planes):
        if bits == 1:
            # MSB of each plane byte holds the first pixel; plane k feeds bit k.
            bit = (row[plane * bpl + (x >> 3)] >> (7 - (x & 7))) & 1
            index |= bit << plane
        else:
            # Whole samples packed MSB first (e.g. 4-bit nibbles in one plane).
            if bits < 8:
                per_byte = 8 // bits
                byte_pos = plane * bpl + x // per_byte
                byte = row[byte_pos]
                shift = 8 - bits - (x % per_byte) * bits
                value = (byte >> shift) & ((1 << bits) - 1)
            else:
                sample_bytes = bits // 8
                byte_pos = plane * bpl + x * sample_bytes
                value = row[byte_pos]
            index |= value << (plane * bits)
    return index


def parse(data):
    if len(data) < 128 or data[0] != 0x0A:
        raise PcxError("not a PCX file")
    version, encoding, bits = data[1], data[2], data[3]
    xmin, ymin, xmax, ymax, hdpi, vdpi = struct.unpack_from("<6H", data, 4)
    ega = data[16:64]
    planes = data[65]
    bpl, palinfo = struct.unpack_from("<HH", data, 66)
    # The image window is inclusive on both edges.
    w, h = xmax - xmin + 1, ymax - ymin + 1
    line = planes * bpl
    if encoding == 1:
        pixels, end, runs = decode(data, 128, line, h)
    else:
        pixels, end, runs = data[128:128 + line * h], 128 + line * h, 0
    vga = None
    # The 256-colour palette is 768 bytes behind a 0x0C marker at the end of
    # the file, regardless of the header version number.
    marker = len(data) - 769
    if bits == 8 and planes == 1 and marker >= end and data[marker] == 0x0C:
        vga = data[marker + 1:]
    kind = ("vga256" if vga else "none") if bits == 8 and planes == 1 else "truecolor" if bits == 8 and planes in (3, 4) else \
        "ega16" if bits * planes == 4 else "mono" if bits * planes == 1 else "other"
    corner = None
    if len(pixels) >= line:
        row = pixels[:line]
        if kind == "truecolor":
            # Each scan line stores whole planes in order: R line, G line, B line[, A line].
            r, g, b = row[0], row[bpl], row[2 * bpl]
        elif kind == "vga256":
            i = row[0]
            # VGA palette components are already full 8-bit values.
            r, g, b = vga[3 * i:3 * i + 3]
        elif kind == "ega16":
            # Header palette applies to every EGA version; plane 0 is the low bit.
            i = _plane_pixel(row, bpl, bits, planes, 0)
            r, g, b = ega[3 * i:3 * i + 3]
        elif kind == "mono":
            r = g = b = 255 if row[0] & 0x80 else 0
        else:
            r = g = b = None
        corner = "#%02x%02x%02x" % (r, g, b) if r is not None else None
    return {"version": version, "rle": encoding == 1, "width": w, "height": h, "bits_per_plane": bits, "planes": planes,
            "bytes_per_line": bpl, "padding_bits": bpl * 8 // bits - w if bits else None, "dpi": [hdpi, vdpi],
            "palette": kind, "grayscale": palinfo == 2, "decoded_lines": len(pixels) // line if line else 0,
            "runs": runs, "complete": len(pixels) == line * h, "corner_pixel": corner}
