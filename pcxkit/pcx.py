"""ZSoft PCX images: header, RLE scan lines, planes, EGA header palette, VGA 256-colour palette."""
import struct


class PcxError(ValueError):
    pass


def decode(data, p, line_bytes, lines):
    """Decode `lines` scan lines of `line_bytes` bytes; runs may cross plane and line boundaries.

    A PCX RLE packet has the top two bits set (0xC0-0xFF); the low six bits are
    the repeat count and the following byte is the value. Any byte below 0xC0,
    including 0x80-0xBF, is a literal pixel value.
    """
    out = bytearray()
    want = line_bytes * lines
    runs = 0
    while len(out) < want and p < len(data):
        b = data[p]
        p += 1
        if b & 0xC0 == 0xC0:
            if p >= len(data):
                break
            count = b & 0x3F
            out += bytes([data[p]]) * count
            p += 1
            runs += 1
        else:
            out.append(b)
    return bytes(out[:want]), p, runs


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
    if encoding == 1:
        pixels, end, runs = decode(data, 128, line, h)
    else:
        pixels, end, runs = data[128:128 + line * h], 128 + line * h, 0
    vga = None
    if bits == 8 and planes == 1 and len(data) >= 769 and data[-769] == 0x0C:
        vga = data[-768:]
    kind = ("vga256" if vga else "none") if bits == 8 and planes == 1 else "truecolor" if bits == 8 and planes in (3, 4) else \
        "ega16" if bits * planes == 4 else "mono" if bits * planes == 1 else "other"
    corner = None
    if len(pixels) >= line:
        row = pixels[:line]
        if kind == "truecolor":
            # Each plane holds one whole scan line: the R plane, then G, then B
            # (an optional fourth plane carries alpha/intensity).
            r, g, b = row[0], row[bpl], row[2 * bpl]
        elif kind == "vga256":
            i = row[0]
            # VGA palette entries already hold full 0-255 component values.
            r, g, b = vga[3 * i:3 * i + 3]
        elif kind == "ega16":
            if bits == 1:
                # One bit per plane, most-significant bit first within a plane
                # byte. Plane 0 supplies the low bit of the colour index.
                i = 0
                for k in range(planes):
                    i |= ((row[k * bpl] >> 7) & 1) << k
            else:
                # Packed pixels (e.g. four bits per pixel in one plane): the
                # first pixel lives in the high nibble of the first byte.
                i = row[0] >> 4
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
