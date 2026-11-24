"""ZSoft PCX images: header, RLE scan lines, planes, EGA header palette, VGA 256-colour palette."""
import struct


class PcxError(ValueError):
    pass


def decode(data, p, line_bytes, lines):
    """Decode `lines` scan lines of `line_bytes` bytes; runs may cross plane and line boundaries."""
    out = bytearray()
    want = line_bytes * lines
    runs = 0
    while len(out) < want and p < len(data):
        b = data[p]
        p += 1
        if b >= 0x80:
            if p >= len(data):
                break
            out += bytes([data[p]]) * (b & 0x7F)
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
    w, h = xmax - xmin, ymax - ymin + 1
    line = planes * bpl
    if encoding == 1:
        pixels, end, runs = decode(data, 128, line, h)
    else:
        pixels, end, runs = data[128:128 + line * h], 128 + line * h, 0
    vga = None
    if version == 5 and bits == 8 and planes == 1 and len(data) >= 769 and data[-769] == 0x0C:
        vga = data[-768:]
    kind = ("vga256" if vga else "none") if bits == 8 and planes == 1 else "truecolor" if bits == 8 and planes in (3, 4) else \
        "ega16" if bits * planes == 4 else "mono" if bits * planes == 1 else "other"
    corner = None
    if len(pixels) >= line:
        row = pixels[:line]
        if kind == "truecolor":
            r, g, b = row[0], row[1], row[2]
        elif kind == "vga256" and vga:
            i = row[0]
            r, g, b = (min(255, v * 4) for v in vga[3 * i:3 * i + 3])
        elif kind == "ega16":
            if planes == 4:
                i = sum(((row[k * bpl] >> 7) & 1) << (3 - k) for k in range(4))
            else:
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
