import struct
import unittest

from sgikit.sgi import HEADER_SIZE, SgiError, parse


def build_header(storage, bpc, dim, width, height, channels,
                 pixmin=0, pixmax=255, cmap=0, name=b""):
    header = struct.pack(">hBBHHHH", 474, storage, bpc, dim,
                         width, height, channels)
    header += struct.pack(">ii", pixmin, pixmax)
    header += b"\0" * 4
    header += name[:80].ljust(80, b"\0")
    header += struct.pack(">i", cmap)
    return header.ljust(HEADER_SIZE, b"\0")


def encode_rle_row(pixels, bpc):
    out = bytearray()
    i = 0
    while i < len(pixels):
        run = 1
        while i + run < len(pixels) and pixels[i + run] == pixels[i] and run < 127:
            run += 1
        if run >= 2:
            out.append(0x80 | run)
            if bpc == 1:
                out.append(pixels[i])
            else:
                out += struct.pack(">H", pixels[i])
            i += run
        else:
            j = i
            while j < len(pixels) and (j + 1 >= len(pixels) or
                                       pixels[j + 1] != pixels[j]) and j - i < 126:
                j += 1
            n = j - i + 1 if j < len(pixels) else len(pixels) - i
            n = min(n, 127)
            out.append(n)
            for value in pixels[i:i + n]:
                if bpc == 1:
                    out.append(value)
                else:
                    out += struct.pack(">H", value)
            i += n
    out.append(0)
    return bytes(out)


def build_sgi(storage, bpc, dim, width, height, channels, pixels,
              shared=False, truncate=0, **header_kwargs):
    """pixels: dict {(y, c): [values]} in display (top-to-bottom) order."""
    eff_h = 1 if dim == 1 else height
    eff_z = 1 if dim in (1, 2) else channels
    height, channels = eff_h, eff_z
    if storage == 0:
        body = bytearray()
        for c in range(channels):
            for stored_y in range(height):
                display_y = height - 1 - stored_y
                row = pixels[(display_y, c)]
                if bpc == 1:
                    body += bytes(row)
                else:
                    body += b"".join(struct.pack(">H", v) for v in row)
        data = (build_header(storage, bpc, dim, width, height, channels,
                             **header_kwargs) + bytes(body))
        if truncate:
            data = data[:-truncate]
        return data

    n = height * channels
    encoded = {}
    for c in range(channels):
        for y in range(height):
            encoded[(y, c)] = encode_rle_row(pixels[(y, c)], bpc)
    offsets = [0] * n
    lengths = [0] * n
    table_end = HEADER_SIZE + 8 * n
    cursor = table_end
    for c in range(channels):
        for y in range(height):
            index = y * channels + c
            blob = encoded[(y, c)]
            if shared and y > 0:
                offsets[index] = offsets[c]
                lengths[index] = lengths[c]
                continue
            offsets[index] = cursor
            lengths[index] = len(blob)
            cursor += len(blob)
    data = bytearray(build_header(storage, bpc, dim, width, height,
                                   channels, **header_kwargs))
    data += struct.pack(">%dI" % n, *offsets)
    data += struct.pack(">%dI" % n, *lengths)
    written = set()
    for c in range(channels):
        for y in range(height):
            index = y * channels + c
            if offsets[index] in written:
                continue
            written.add(offsets[index])
            data += encoded[(y, c)]
    if truncate:
        data = data[:-truncate]
    return bytes(data)


def gradient(w, h, z, bpc):
    high = (1 << (8 * bpc)) - 1
    pixels = {}
    for c in range(z):
        for y in range(h):
            pixels[(y, c)] = [
                (y * w + x + c * 7) % (high + 1) for x in range(w)
            ]
    return pixels


class VerbatimTests(unittest.TestCase):
    def test_8bit_three_channel_planar_and_row_order(self):
        w, h, z = 5, 4, 3
        pixels = gradient(w, h, z, 1)
        out = parse(build_sgi(0, 1, 3, w, h, z, pixels))
        self.assertEqual(out["storage"], "verbatim")
        self.assertTrue(out["complete"])
        self.assertEqual(out["shared_rows"], 0)
        self.assertEqual(out["points"]["top_left"],
                         [pixels[(0, c)][0] for c in range(z)])
        self.assertEqual(out["points"]["top_right"],
                         [pixels[(0, c)][w - 1] for c in range(z)])
        self.assertEqual(out["points"]["bottom_left"],
                         [pixels[(h - 1, c)][0] for c in range(z)])
        self.assertEqual(out["points"]["center"],
                         [pixels[(h // 2, c)][w // 2] for c in range(z)])
        self.assertEqual(out["max_value"],
                         max(v for row in pixels.values() for v in row))

    def test_16bit_channels(self):
        w, h = 4, 3
        pixels = gradient(w, h, 1, 2)
        out = parse(build_sgi(0, 2, 2, w, h, 1, pixels,
                              pixmax=65535))
        self.assertEqual(out["bytes_per_channel"], 2)
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [pixels[(0, 0)][0]])
        self.assertEqual(out["max_value"],
                         max(v for row in pixels.values() for v in row))

    def test_16bit_rle(self):
        w, h, z = 6, 2, 2
        pixels = gradient(w, h, z, 2)
        out = parse(build_sgi(1, 2, 3, w, h, z, pixels, pixmax=65535))
        self.assertEqual(out["storage"], "rle")
        self.assertEqual(out["bytes_per_channel"], 2)
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["bottom_left"],
                         [pixels[(h - 1, c)][0] for c in range(z)])

    def test_dimension_one_is_a_single_row(self):
        w = 8
        pixels = {(0, 0): [(x * 31) % 256 for x in range(w)]}
        data = build_sgi(0, 1, 1, w, 99, 7, pixels)
        out = parse(data)
        self.assertEqual((out["width"], out["height"], out["channels"]),
                         (w, 1, 1))
        self.assertEqual(out["points"]["top_left"], [pixels[(0, 0)][0]])
        self.assertEqual(out["points"]["top_right"], [pixels[(0, 0)][-1]])

    def test_dimension_two_is_single_channel(self):
        w, h = 4, 4
        pixels = gradient(w, h, 1, 1)
        data = build_sgi(0, 1, 2, w, h, 9, pixels)
        out = parse(data)
        self.assertEqual((out["width"], out["height"], out["channels"]),
                         (w, h, 1))


class RleTests(unittest.TestCase):
    def test_rle_three_channel_no_cross_talk(self):
        w, h, z = 7, 5, 3
        pixels = {}
        for c in range(z):
            for y in range(h):
                pixels[(y, c)] = [10 + c * 50 + y * 5 + x for x in range(w)]
        out = parse(build_sgi(1, 1, 3, w, h, z, pixels))
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [10, 60, 110])
        self.assertEqual(out["points"]["top_right"],
                         [10 + w - 1, 60 + w - 1, 110 + w - 1])
        self.assertEqual(out["points"]["center"],
                         [10 + (h // 2) * 5 + w // 2,
                          60 + (h // 2) * 5 + w // 2,
                          110 + (h // 2) * 5 + w // 2])

    def test_run_packets_decode_correctly(self):
        w = 20
        row = [7] * 8 + list(range(8)) + [9] * 4
        pixels = {(0, 0): row}
        out = parse(build_sgi(1, 1, 1, w, 1, 1, pixels))
        self.assertTrue(out["complete"])
        self.assertEqual(out["max_value"], 9)
        self.assertEqual(out["points"]["top_left"], [7])

    def test_shared_rows(self):
        w, h, z = 4, 6, 1
        pixels = {(y, 0): [(y + 1) * 10 + x for x in range(w)]
                  for y in range(h)}
        out = parse(build_sgi(1, 1, 2, w, h, z, pixels, shared=True))
        # Every entry after the first per channel reuses an earlier offset.
        self.assertEqual(out["shared_rows"], h - 1)
        self.assertTrue(out["complete"])

    def test_truncated_rle_row_keeps_partial_decode(self):
        w = 12
        row = [5] * 4 + [6] * 8
        pixels = {(0, 0): row}
        full = build_sgi(1, 1, 1, w, 1, 1, pixels)
        # Remove the terminator and the tail of the 6-run: the literal run
        # of four 5s still decodes, while the row never reaches full width.
        out = parse(full[:-2])
        self.assertFalse(out["complete"])
        self.assertEqual(out["points"]["top_left"], [5])
        self.assertIsNone(out["points"]["top_right"])
        self.assertIsNone(out["points"]["center"])
        self.assertEqual(out["max_value"], 5)

    def test_truncated_table_does_not_raise(self):
        w, h, z = 3, 4, 2
        pixels = gradient(w, h, z, 1)
        data = build_sgi(1, 1, 3, w, h, z, pixels)
        out = parse(data[:HEADER_SIZE + 4])
        self.assertFalse(out["complete"])
        self.assertIsNone(out["points"]["top_left"])
        self.assertIsNone(out["max_value"])

    def test_rle_16bit_truncated(self):
        w, h = 5, 2
        pixels = gradient(w, h, 1, 2)
        full = build_sgi(1, 2, 2, w, h, 1, pixels, pixmax=65535)
        out = parse(full[:-3])
        self.assertFalse(out["complete"])
        self.assertIsNotNone(out["max_value"])


class TruncationAndHeaderTests(unittest.TestCase):
    def test_verbatim_truncated_row(self):
        w, h = 6, 2
        pixels = gradient(w, h, 1, 1)
        data = build_sgi(0, 1, 2, w, h, 1, pixels, truncate=8)
        out = parse(data)
        self.assertFalse(out["complete"])
        # Rows are stored bottom-to-top, so the last plane row is the
        # display top row and is the one lost to truncation.
        self.assertIsNone(out["points"]["top_left"])
        self.assertIsNotNone(out["points"]["bottom_left"])
        self.assertIsNotNone(out["max_value"])

    def test_bad_magic_and_short_file(self):
        with self.assertRaises(SgiError):
            parse(bytes(512))
        with self.assertRaises(SgiError):
            parse(b"\0" * 10)

    def test_bad_storage_bpc_dimension(self):
        base = bytearray(build_header(0, 1, 3, 2, 2, 1)) + bytes(8)
        bad_storage = bytearray(base)
        bad_storage[2] = 9
        with self.assertRaises(SgiError):
            parse(bytes(bad_storage))
        bad_bpc = bytearray(base)
        bad_bpc[3] = 3
        with self.assertRaises(SgiError):
            parse(bytes(bad_bpc))
        bad_dim = bytearray(base)
        bad_dim[4] = 4
        with self.assertRaises(SgiError):
            parse(bytes(bad_dim))

    def test_colormap_names_and_name_field(self):
        data = build_sgi(0, 1, 1, 1, 1, 1, {(0, 0): [1]},
                         cmap=3, name=b"slate")
        out = parse(data)
        self.assertEqual(out["colormap"], "colormap")
        self.assertEqual(out["name"], "slate")
        odd = build_sgi(0, 1, 1, 1, 1, 1, {(0, 0): [1]}, cmap=7)
        self.assertEqual(parse(odd)["colormap"], "type_7")
        empty = build_sgi(0, 1, 1, 1, 1, 1, {(0, 0): [1]})
        self.assertIsNone(parse(empty)["name"])


if __name__ == "__main__":
    unittest.main()
