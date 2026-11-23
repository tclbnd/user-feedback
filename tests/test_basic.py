import unittest

from proreskit.prores import parse

from streams import frame, stream


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        out = parse(stream(1, width=1280, height=720))
        self.assertEqual((out["width"], out["height"], out["frames"], out["creator"]), (1280, 720, 1, "apl0"))

    def test_slices(self):
        self.assertEqual(parse(stream(1, slices=12))["slices"], 12)

    def test_multiple_frames(self):
        out = parse(stream(7))
        self.assertEqual(out["frames"], 7)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["min_frame_bytes"], out["max_frame_bytes"])

    def test_chroma(self):
        self.assertEqual(parse(stream(1, chroma=2))["chroma"], "4:2:2")
        self.assertEqual(parse(stream(1, chroma=3))["chroma"], "4:4:4")

    def test_frame_rates(self):
        rates = {1: "24000/1001", 2: "24", 3: "25", 4: "30000/1001", 5: "30",
                 6: "50", 7: "60000/1001", 8: "60"}
        for code, text in rates.items():
            self.assertEqual(parse(stream(1, rate=code))["frame_rate"], text, code)
        self.assertIsNone(parse(stream(1, rate=0))["frame_rate"])

    def test_interlaced(self):
        out = parse(stream(1, scan=1, slices=5))
        self.assertEqual(out["scan"], "interlaced_tff")
        self.assertEqual((out["pictures"], out["slices"]), (2, 10))
        self.assertEqual(parse(stream(1, scan=2))["scan"], "interlaced_bff")

    def test_alpha(self):
        self.assertIsNone(parse(stream(1, alpha=0))["alpha_bits"])
        self.assertEqual(parse(stream(1, alpha=1))["alpha_bits"], 8)
        self.assertEqual(parse(stream(1, alpha=2))["alpha_bits"], 16)

    def test_custom_matrices_extend_header(self):
        out = parse(stream(2, matrices=3, extra_header=6))
        self.assertTrue(out["custom_matrices"])
        self.assertEqual((out["frames"], out["inconsistent_frames"]), (2, 0))

    def test_inconsistent_frame(self):
        data = frame(bad_slice=True)
        self.assertEqual(parse(data)["inconsistent_frames"], 1)
        self.assertEqual(parse(data + data)["inconsistent_frames"], 2)

    def test_truncated(self):
        full = stream(3)
        out = parse(full[:len(full) - 10])
        self.assertEqual((out["frames"], out["truncated"]), (2, True))
        out = parse(full + b"\x00\x00")
        self.assertTrue(out["truncated"])
