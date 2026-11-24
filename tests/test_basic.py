import unittest

from itkit.it import parse

from mods import minimal, module


class BasicTests(unittest.TestCase):
    def test_title(self):
        out = parse(minimal())
        self.assertEqual((out["title"], out["speed"], out["tempo"]), ("Test", 6, 125))

    def test_empty_lists(self):
        out = parse(minimal())
        self.assertEqual((out["samples"], out["pattern_rows"]), ([], []))

    def test_versions(self):
        out = parse(module(cwt=0x0214, cmwt=0x0217))
        self.assertEqual((out["tracker"], out["compatible"]), ("2.14", "2.17"))
        self.assertEqual(parse(module(cwt=0x0100, cmwt=0x0200))["tracker"], "1.00")
        self.assertEqual(parse(module(cwt=0x0230))["tracker"], "2.30")

    def test_orders_markers(self):
        out = parse(module(orders=[0, 1, 254, 2, 1, 255, 3]))
        self.assertEqual(out["orders"], [0, 1, 2, 1])

    def test_orders_without_end_marker(self):
        self.assertEqual(parse(module(orders=[0, 1]))["orders"], [0, 1])

    def test_channels_only_enabled(self):
        pans = [32, 100, 160, 0, 64, 200, 100] + [160] * 57
        out = parse(module(pans=pans))
        self.assertEqual((out["channels"], out["surround_channels"]), (5, 2))

    def test_sample_data_bytes(self):
        out = parse(module(samples=[(0x01, 4000), (0x03, 12000), (0x07, 30000),
                                    (0x0f, 5000), (0x00, 1000)]))
        got = [(s["bits"], s["stereo"], s["data_bytes"]) for s in out["samples"]]
        self.assertEqual(got, [(8, False, 4000), (16, False, 24000), (16, True, 120000),
                               (16, True, None), (8, False, None)])

    def test_pattern_rows(self):
        out = parse(module(patterns=[64, None, 32, 0]))
        self.assertEqual(out["pattern_rows"], [64, 64, 32, 64])
