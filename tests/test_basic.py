import struct
import unittest

from sgikit.sgi import SgiError, parse


def header(storage, bpc, dim, w, h, z, name=b"test", pixmin=0, pixmax=255, cmap=0):
    return (struct.pack(">hBBHHHH", 474, storage, bpc, dim, w, h, z)
            + struct.pack(">ii", pixmin, pixmax) + bytes(4) + name.ljust(80, b"\0")
            + struct.pack(">i", cmap) + bytes(404))


def rle_row(values, bpc):
    """Literal packet for the whole row, then a zero count."""
    fmt = ">B" if bpc == 1 else ">H"
    pack = lambda v: struct.pack(fmt, v)
    out = pack(0x80 | len(values)) + b"".join(pack(v) for v in values)
    return out + pack(0)


def verbatim(storage, bpc, dim, planes, name=b"test", pixmin=0, pixmax=255):
    """planes[c][y] is the row y (file order, bottom to top) of channel c."""
    h = len(planes[0])
    w = len(planes[0][0])
    head = header(storage, bpc, dim, w, h, len(planes), name, pixmin, pixmax)
    fmt = ">%d%s" % (w, "B" if bpc == 1 else "H")
    body = b"".join(struct.pack(fmt, *row) for c in range(len(planes)) for row in planes[c])
    return head + body


def rle(planes, bpc=1, name=b"test", dim=None, shared=None):
    h = len(planes[0])
    w = len(planes[0][0])
    z = len(planes)
    dim = dim or (1 if h == 1 else 3)
    chunks = [rle_row(planes[c][y], bpc) for c in range(z) for y in range(h)]
    table_start = 512 + 2 * 4 * h * z                  # offset table + length table
    starts, p = [], table_start
    for chunk in chunks:
        starts.append(p)
        p += len(chunk)
    if shared is not None:                               # make entry `shared` reuse entry 0
        starts[shared] = starts[0]
    head = header(1, bpc, dim, w, h, z, name)
    lengths = [len(c) for c in chunks]
    return head + struct.pack(">%dI" % len(starts), *starts) + struct.pack(">%dI" % len(lengths), *lengths) + b"".join(chunks)


def jump_offset(data, i):
    """i-th entry of the RLE offset table."""
    return struct.unpack_from(">I", data, 512 + 4 * i)[0]


class BasicTests(unittest.TestCase):
    def test_header(self):
        hd = struct.pack(">hBBHHHH", 474, 0, 1, 2, 2, 1, 1) + bytes(498) + b"\x05\x06"
        out = parse(hd)
        self.assertEqual((out["storage"], out["width"], out["channels"]), ("verbatim", 2, 1))

    def test_magic(self):
        with self.assertRaises(SgiError):
            parse(bytes(600))


class SpecTests(unittest.TestCase):
    def test_rle_literal_and_repeat(self):
        # one row: literal 1 2 3 then repeat 9 twice
        head = header(1, 1, 1, 5, 1, 1)
        row = bytes([0x83, 1, 2, 3, 0x02, 9, 0x00])
        data = head + struct.pack(">II", 512 + 8, len(row)) + row
        out = parse(data)
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [1])
        self.assertEqual(out["points"]["top_right"], [9])

    def test_verbatim_planes_and_row_order(self):
        red = [[10, 11], [20, 21]]                       # file order: bottom row then top row
        green = [[30, 31], [40, 41]]
        blue = [[50, 51], [60, 61]]
        out = parse(verbatim(0, 1, 3, [red, green, blue]))
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [20, 40, 60])
        self.assertEqual(out["points"]["bottom_left"], [10, 30, 50])
        self.assertEqual(out["points"]["top_right"], [21, 41, 61])

    def test_rle_channels_do_not_cross(self):
        red = [[1, 2], [3, 4]]
        green = [[5, 6], [7, 8]]
        blue = [[9, 10], [11, 12]]
        out = parse(rle([red, green, blue]))
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [3, 7, 11])
        self.assertEqual(out["points"]["bottom_left"], [1, 5, 9])

    def test_two_byte_channels(self):
        plane = [[0, 65535, 258], [1000, 2000, 3000]]
        out = parse(verbatim(0, 2, 2, [plane], pixmax=65535))
        self.assertTrue(out["complete"])
        self.assertEqual(out["width"], 3)
        self.assertEqual(out["points"]["top_left"], [1000])
        self.assertEqual(out["max_value"], 65535)        # the top row holds 258, 1000, 2000, 3000
        out = parse(rle([plane], bpc=2, dim=2))
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], [1000])

    def test_dimension_one_is_single_row(self):
        out = parse(verbatim(0, 1, 1, [[[1, 2, 3]]]))
        self.assertEqual((out["height"], out["channels"]), (1, 1))
        self.assertEqual(out["points"]["center"], [2])

    def test_shared_rows(self):
        planes = [[[i] for i in range(6)]]
        data = rle(planes, shared=5)                     # top row's entry reuses row 0's data
        out = parse(data)
        self.assertEqual(out["shared_rows"], 1)
        self.assertEqual(out["points"]["top_left"], [0])  # row 5's entry reuses row 0's data
        self.assertEqual(jump_offset(data, 5), jump_offset(data, 0))
        plain = parse(rle(planes))
        self.assertEqual(plain["shared_rows"], 0)
        self.assertEqual(plain["points"]["top_left"], [5])
        self.assertEqual(parse(verbatim(0, 1, 2, planes[:1]))["shared_rows"], 0)

    def test_truncated_is_partial_not_error(self):
        good = rle([[[1, 2], [3, 4]]])
        out = parse(good[:jump_offset(good, 1)])          # tables intact, second row's payload cut
        self.assertFalse(out["complete"])
        self.assertEqual(out["points"]["bottom_left"], [1])   # file row 0 survives
        self.assertIsNone(out["points"]["top_left"])          # file row 1 was cut off
        out = parse(good[:512 + 8])                      # offset table only partly there
        self.assertFalse(out["complete"])
        self.assertIsNone(out["points"]["top_left"])
        fuller = parse(verbatim(0, 1, 3, [[[1, 2]], [[3, 4]]]))
        self.assertTrue(fuller["complete"])
        out = parse(verbatim(0, 1, 3, [[[1, 2]], [[3, 4]]])[:-1])   # last channel short by a byte
        self.assertFalse(out["complete"])

