import unittest
from fractions import Fraction

from hcakit.hca import parse

from files import hca


class BasicTests(unittest.TestCase):
    def test_layout(self):
        out = parse(hca(blocks=300, bsize=0x200))
        self.assertEqual((out["version"], out["channels"], out["blocks"], out["block_size"]), ("2.0", 2, 300, 512))

    def test_comment(self):
        out = parse(hca(comment="vo_001", cipher=0))
        self.assertEqual((out["comment"], out["cipher"], out["loop"]), ("vo_001", "none", None))

    def test_sample_rate_is_24_bit(self):
        # 33602432 is what you get when the channel count is read as part of the rate
        out = parse(hca(rate=48000))
        self.assertEqual(out["sample_rate"], 48000)

    def test_samples_skip_delay_and_padding(self):
        out = parse(hca(blocks=300, delay=128, padding=412))
        self.assertEqual((out["encoder_delay"], out["padding"]), (128, 412))
        self.assertEqual(out["samples"], 300 * 1024 - 128 - 412)
        self.assertEqual(out["duration"], str(Fraction(300 * 1024 - 128 - 412, 48000)))

    def test_masked_names(self):
        out = parse(hca(mask=True, bsize=0x100))
        self.assertEqual((out["masked"], out["block_size"]), (True, 0x100))

    def test_dec_chunk_older_encoder(self):
        out = parse(hca(dec=True, bsize=0x80))
        self.assertEqual((out["block_size"], out["vbr"]), (0x80, False))

    def test_loop_end_keeps_end_padding(self):
        out = parse(hca(loop=(10, 20, 128, 300), delay=128))
        self.assertEqual(out["loop"], {"start": 10 * 1024, "end": 20 * 1024 + 300 - 128})

    def test_cipher(self):
        self.assertEqual(parse(hca(cipher=0))["cipher"], "none")
        self.assertEqual(parse(hca(cipher=1))["cipher"], "keyless")
        self.assertEqual(parse(hca(cipher=56))["cipher"], "keyed")

    def test_header_crc(self):
        self.assertTrue(parse(hca())["header_crc_ok"])
        self.assertFalse(parse(hca(bad_crc=True))["header_crc_ok"])

    def test_vbr(self):
        out = parse(hca(bsize=0, vbr=1))
        self.assertEqual((out["vbr"], out["block_size"], out["truncated"]), (True, None, None))

    def test_truncated_counts_payload_only(self):
        self.assertFalse(parse(hca(blocks=10, bsize=64))["truncated"])
        self.assertTrue(parse(hca(blocks=10, bsize=64, cut=1))["truncated"])

    def test_volume(self):
        self.assertEqual(parse(hca(volume=0.5))["volume"], 0.5)
