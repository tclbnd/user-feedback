"""Minimal PNG codec and texture sampler (stdlib only).

Decodes 8-bit/16-bit PNG files (color types 0/2/3/6, interlace method 0
or 1) using only zlib + struct, and provides a bilinear sampler with
repeat wrapping.  Also contains an 8-bit RGB PNG encoder used to write
rendered frames.
"""

import struct
import zlib


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, width, height, channels, bit_depth):
    """Reverse PNG scanline filters (0-4). Returns flat sample bytes."""
    bpp = max(1, channels * bit_depth // 8)  # bytes per complete pixel
    stride = (width * channels * bit_depth + 7) // 8
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        # filter type 0: None
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def _deinterlace_adam7(data, width, height, channels, bit_depth):
    """Recombine Adam7 interlaced passes into one flat sample buffer."""
    bps = bit_depth // 8 if bit_depth >= 8 else 1
    pixel_bytes = channels * bps
    starts = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
              (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    out = bytearray(len(data))
    src = 0
    for x0, y0, dx, dy in starts:
        pw = (width - x0 + dx - 1) // dx
        ph = (height - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        stride = (pw * channels * bit_depth + 7) // 8
        pass_len = stride * ph
        pass_data = data[src:src + pass_len]
        src += pass_len
        for py in range(ph):
            for px in range(pw):
                sx = px * pixel_bytes
                dx_idx = (x0 + px * dx) * pixel_bytes
                dy_idx = (y0 + py * dy) * width * pixel_bytes
                out[dy_idx + dx_idx:dy_idx + dx_idx + pixel_bytes] = \
                    pass_data[py * stride + sx:py * stride + sx + pixel_bytes]
    return out


def _read_chunks(data):
    if data[:8] != PNG_SIGNATURE:
        raise ValueError('not a PNG file (bad signature)')
    pos = 8
    chunks = []
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack('>I', data[pos + 8 + length:pos + 12 + length])
        if crc != zlib.crc32(ctype + body) & 0xFFFFFFFF:
            raise ValueError('PNG CRC mismatch in chunk %r' % ctype)
        chunks.append((ctype, body))
        pos += 12 + length
    return chunks


def load_png(path):
    """Load a PNG into (width, height, bytes) with 8-bit RGB samples."""
    with open(path, 'rb') as fh:
        data = fh.read()
    chunks = _read_chunks(data)
    if chunks[0][0] != b'IHDR':
        raise ValueError('PNG missing IHDR')
    width, height, bit_depth, color_type, comp, filt, interlace = \
        struct.unpack('>IIBBBBB', chunks[0][1])
    if comp != 0 or filt != 0:
        raise ValueError('unsupported PNG compression/filter method')
    channel_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channel_map:
        raise ValueError('unsupported PNG color type %d' % color_type)
    channels = channel_map[color_type]

    idat = b''.join(body for ctype, body in chunks if ctype == b'IDAT')
    if not idat:
        raise ValueError('PNG has no IDAT chunks')
    raw = zlib.decompress(idat)
    stride = (width * channels * bit_depth + 7) // 8
    expected = (stride + 1) * height
    if interlace == 1:
        # Adam7: size differs; reconstruct per pass instead.
        pixels = _deinterlace_adam7(
            _unfilter_interlaced(raw, width, height, channels, bit_depth),
            width, height, channels, bit_depth)
    else:
        if len(raw) != expected:
            raise ValueError('PNG IDAT size mismatch')
        pixels = _unfilter(raw, width, height, channels, bit_depth)

    plte = None
    trns = None
    for ctype, body in chunks:
        if ctype == b'PLTE':
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        if ctype == b'tRNS':
            trns = body

    return _to_rgb8(pixels, width, height, channels, bit_depth,
                    color_type, plte, trns)


def _unfilter_interlaced(raw, width, height, channels, bit_depth):
    starts = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
              (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    out = bytearray()
    pos = 0
    for x0, y0, dx, dy in starts:
        pw = (width - x0 + dx - 1) // dx
        ph = (height - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        stride = (pw * channels * bit_depth + 7) // 8
        pass_len = (stride + 1) * ph
        out += _unfilter(raw[pos:pos + pass_len], pw, ph, channels, bit_depth)
        pos += pass_len
    return out


def _to_rgb8(pixels, width, height, channels, bit_depth, color_type,
             plte, trns):
    """Normalize any supported pixel format to 8-bit RGB bytearray."""
    out = bytearray(width * height * 3)
    if bit_depth < 8:
        # unpack sub-byte samples (1/2/4-bit grayscale or indexed)
        unpacked = bytearray()
        mask = (1 << bit_depth) - 1
        per_byte = 8 // bit_depth
        for byte in pixels:
            for shift in range(per_byte - 1, -1, -1):
                unpacked.append((byte >> (shift * bit_depth)) & mask)
        pixels = unpacked

    if bit_depth == 16:
        # take high byte of each sample
        pixels = pixels[0::2]

    oi = 0
    for i in range(width * height):
        if color_type == 0:  # grayscale
            g = pixels[i]
            out[oi:oi + 3] = bytes((g, g, g))
        elif color_type == 2:  # RGB
            s = i * 3
            out[oi:oi + 3] = pixels[s:s + 3]
        elif color_type == 3:  # indexed
            idx = pixels[i]
            out[oi:oi + 3] = bytes(plte[idx])
        elif color_type == 4:  # grayscale + alpha -> drop alpha
            g = pixels[i * 2]
            out[oi:oi + 3] = bytes((g, g, g))
        else:  # color_type 6: RGBA -> drop alpha
            s = i * 4
            out[oi:oi + 3] = pixels[s:s + 3]
        oi += 3
    return width, height, bytes(out)


class Texture:
    """Bilinear RGBA-free RGB texture with repeat (wrap) addressing."""

    def __init__(self, width, height, rgb_bytes):
        self.width = width
        self.height = height
        self.data = rgb_bytes

    @classmethod
    def from_file(cls, path):
        w, h, data = load_png(path)
        return cls(w, h, data)

    def sample_nearest(self, u, v):
        w, h = self.width, self.height
        x = int(u * w) % w
        y = int(v * h) % h
        i = (y * w + x) * 3
        d = self.data
        return float(d[i]), float(d[i + 1]), float(d[i + 2])

    def sample(self, u, v):
        """Bilinear sample; u/v wrap modulo 1."""
        w, h = self.width, self.height
        fx = (u * w) % w
        fy = (v * h) % h
        x0 = int(fx)
        y0 = int(fy)
        tx = fx - x0
        ty = fy - y0
        x1 = (x0 + 1) % w
        y1 = (y0 + 1) % h
        d = self.data
        i00 = (y0 * w + x0) * 3
        i10 = (y0 * w + x1) * 3
        i01 = (y1 * w + x0) * 3
        i11 = (y1 * w + x1) * 3
        rgb = []
        for k in range(3):
            top = d[i00 + k] * (1.0 - tx) + d[i10 + k] * tx
            bot = d[i01 + k] * (1.0 - tx) + d[i11 + k] * tx
            rgb.append(top * (1.0 - ty) + bot * ty)
        return rgb


def write_png(path, width, height, rgb_bytes):
    """Encode an 8-bit RGB image as a non-interlaced PNG (filter 0)."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type: None
        raw += rgb_bytes[y * stride:(y + 1) * stride]
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(ctype, body):
        return (struct.pack('>I', len(body)) + ctype + body +
                struct.pack('>I', zlib.crc32(ctype + body) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    with open(path, 'wb') as fh:
        fh.write(PNG_SIGNATURE)
        fh.write(chunk(b'IHDR', ihdr))
        fh.write(chunk(b'IDAT', compressed))
        fh.write(chunk(b'IEND', b''))
