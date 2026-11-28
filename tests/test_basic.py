import unittest

from cyuvkit.cyuv import CyuvError, frame, parse


def example(path):
    with open(path, "rb") as fh:
        return fh.read()


def first_frame_buf(data):
    import struct
    n, = struct.unpack_from("<I", data, 18)
    return data[22:22 + n]


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        out = parse(example("examples/clip.cyuv"))
        self.assertEqual((out["width"], out["height"], out["codec"], len(out["frames"])), (32, 8, "CYUV", 3))

    def test_bad_magic(self):
        with self.assertRaises(CyuvError):
            parse(bytes(32))

    def test_unknown_codec_rejected(self):
        data = bytearray(example("examples/clip.cyuv"))
        data[12:16] = b"XVID"
        with self.assertRaises(CyuvError):
            parse(bytes(data))

    def test_aura_header(self):
        out = parse(example("examples/logo.aura"))
        self.assertEqual((out["width"], out["height"], out["codec"], len(out["frames"])), (32, 8, "AURA", 1))
        self.assertEqual(out["frames"][0]["state"], "decoded")
        self.assertEqual(out["frames"][0]["format"], "yuv411p")

    def test_row_start_predictors(self):
        # first pixel of each row comes from the high nibble of the first
        # byte of the row, so it must be a real luma level, not near black
        fmt, pic = frame(first_frame_buf(example("examples/clip.cyuv")), 32, 8)
        self.assertEqual(fmt, "yuv411p")
        y = pic[:32 * 8]
        for row in range(8):
            self.assertGreaterEqual(y[row * 32], 0x40)

    def test_aura_swaps_chroma(self):
        # AURA decodes like CYUV but with the U and V planes exchanged
        cyuv = first_frame_buf(example("examples/clip.cyuv"))
        aura = first_frame_buf(example("examples/logo.aura"))
        self.assertEqual(cyuv, aura)
        fmt_c, pic_c = frame(cyuv, 32, 8)
        fmt_a, pic_a = frame(aura, 32, 8, swap_uv=True)
        self.assertEqual((fmt_c, fmt_a), ("yuv411p", "yuv411p"))
        n_y = 32 * 8
        n_c = 8 * 8
        self.assertEqual(pic_c[:n_y], pic_a[:n_y])
        self.assertEqual(pic_c[n_y:n_y + n_c], pic_a[n_y + n_c:])
        self.assertEqual(pic_c[n_y + n_c:], pic_a[n_y:n_y + n_c])

if __name__ == "__main__":
    unittest.main()
