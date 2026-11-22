import struct
import unittest

from hcakit.hca import HcaError, parse

from files import hca


class FormatTests(unittest.TestCase):
    def test_sample_rate_is_24bit(self):
        # The rate field is three bytes; reading it as a uint32 used to bleed
        # the channel byte in and report rates above 30 million.
        out = parse(hca(channels=2, rate=44100))
        self.assertEqual(out["sample_rate"], 44100)
        out = parse(hca(channels=1, rate=24000))
        self.assertEqual((out["channels"], out["sample_rate"]), (1, 24000))

    def test_playable_samples_drop_delay_and_padding(self):
        out = parse(hca(blocks=10, delay=128, padding=300))
        self.assertEqual(out["samples"], 10 * 1024 - 128 - 300)
        self.assertEqual(out["duration"], "2453/12000")
        self.assertEqual(out["encoder_delay"], 128)
        self.assertEqual(out["padding"], 300)

    def test_masked_file_is_recognised(self):
        out = parse(hca(cipher=1, mask=True))
        self.assertTrue(out["masked"])
        self.assertEqual(out["cipher"], "keyless")

    def test_unmasked_file(self):
        out = parse(hca(cipher=0))
        self.assertFalse(out["masked"])
        self.assertEqual(out["cipher"], "none")

    def test_cipher_classification(self):
        self.assertEqual(parse(hca(cipher=0))["cipher"], "none")
        self.assertEqual(parse(hca(cipher=1))["cipher"], "keyless")
        self.assertEqual(parse(hca(cipher=56))["cipher"], "keyed")
        self.assertEqual(parse(hca(cipher=0x38, mask=True))["cipher"], "keyed")

    def test_loop_points_are_playable_exclusive(self):
        # Absolute loop timeline includes the 128-sample encoder delay and the
        # sub-frame offsets; the end sample is exclusive.
        out = parse(hca(blocks=300, delay=128, padding=300, loop=(10, 200, 64, 512)))
        self.assertEqual(out["loop"], {
            "start": 10 * 1024 + 64 - 128,
            "end": 200 * 1024 + 512 - 128,
        })

    def test_loop_end_keeps_subframe_offset(self):
        # Ignoring the in-frame offsets used to cut the loop short by 512.
        out = parse(hca(blocks=300, delay=0, padding=0, loop=(0, 10, 0, 512)))
        self.assertEqual(out["loop"]["end"], 10 * 1024 + 512)

    def test_header_crc_good_and_bad(self):
        self.assertTrue(parse(hca(bad_crc=False))["header_crc_ok"])
        self.assertFalse(parse(hca(bad_crc=True))["header_crc_ok"])
        # CRC is checked over the bytes as stored, so masked headers count too.
        self.assertTrue(parse(hca(mask=True, bad_crc=False))["header_crc_ok"])
        self.assertFalse(parse(hca(mask=True, bad_crc=True))["header_crc_ok"])

    def test_vbr_file(self):
        out = parse(hca(bsize=0, vbr=0x100))
        self.assertTrue(out["vbr"])
        self.assertIsNone(out["block_size"])
        self.assertIsNone(out["truncated"])

    def test_cbr_truncation_uses_frame_region(self):
        full = parse(hca(blocks=20))
        self.assertFalse(full["truncated"])
        cut = 500
        short = parse(hca(blocks=20, cut=cut))
        self.assertTrue(short["truncated"])

    def test_dec_chunk_version_1_3(self):
        out = parse(hca(version=0x0103, dec=True))
        self.assertEqual(out["version"], "1.3")
        self.assertEqual(out["block_size"], 0x200)

    def test_rva_volume_is_float(self):
        out = parse(hca(volume=0.5))
        self.assertAlmostEqual(out["volume"], 0.5)
        self.assertEqual(parse(hca())["volume"], 1.0)

    def test_ath_type(self):
        self.assertEqual(parse(hca(ath=1))["ath_type"], 1)

    def test_not_hca(self):
        with self.assertRaises(HcaError):
            parse(b"RIFF" + b"\0" * 64)
        with self.assertRaises(HcaError):
            parse(b"\x00\x01\x02")

    def test_comment_roundtrip(self):
        out = parse(hca(comment="vo_001", mask=True))
        self.assertEqual(out["comment"], "vo_001")


class ExampleFileTests(unittest.TestCase):
    def test_bgm_battle(self):
        with open("examples/bgm_battle.hca", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(out["version"], "2.0")
        self.assertFalse(out["masked"])
        self.assertTrue(out["header_crc_ok"])
        self.assertEqual((out["channels"], out["sample_rate"]), (2, 48000))
        self.assertEqual((out["blocks"], out["block_size"]), (2900, 512))
        self.assertEqual((out["encoder_delay"], out["padding"]), (128, 412))
        self.assertEqual(out["samples"], 2900 * 1024 - 128 - 412)
        self.assertEqual(out["loop"], {"start": 96256, "end": 2928924})
        self.assertEqual(out["cipher"], "keyless")
        self.assertEqual(out["comment"], "bgm_battle_loop")
        self.assertFalse(out["truncated"])

    def test_vo_intro(self):
        with open("examples/vo_intro.hca", "rb") as fh:
            out = parse(fh.read())
        self.assertTrue(out["masked"])
        self.assertTrue(out["header_crc_ok"])
        self.assertEqual((out["channels"], out["sample_rate"]), (1, 24000))
        self.assertEqual(out["cipher"], "keyed")
        self.assertEqual(out["samples"], 180 * 1024 - 128 - 60)
        self.assertIsNone(out["loop"])
        self.assertFalse(out["truncated"])


if __name__ == "__main__":
    unittest.main()
