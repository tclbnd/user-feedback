import os
import unittest

from rstmkit.rstm import RstmError, parse

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class BasicTests(unittest.TestCase):
    def test_magic(self):
        with self.assertRaises(RstmError):
            parse(b"RIFF" + bytes(60))

    def test_bom(self):
        with self.assertRaises(RstmError):
            parse(b"RSTM\x00\x00" + bytes(60))


class ExampleTests(unittest.TestCase):
    def _load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_title_theme(self):
        # DSP-ADPCM, 2 channels, 32 kHz, loops, 90 seconds.
        r = self._load("title_theme.brstm")
        self.assertEqual(r["version"], "1.0")
        self.assertEqual(r["byte_order"], "big")
        self.assertEqual(r["codec"], "dsp_adpcm")
        self.assertEqual(r["channels"], 2)
        self.assertEqual(r["sample_rate"], 32000)
        self.assertTrue(r["loop"])
        self.assertEqual(r["loop_start"], 211200)
        self.assertEqual(r["total_samples"], 2880000)
        self.assertEqual(r["duration"], "90")
        self.assertEqual(r["blocks"], 201)
        self.assertEqual(r["block_samples"], 14336)
        self.assertEqual(r["last_block_samples"], 12800)
        self.assertEqual(r["seek_interval"], 4)
        self.assertEqual(r["expected_data_bytes"], r["data_bytes"])
        self.assertFalse(r["truncated"])
        self.assertEqual(r["adpcm"], [])

    def test_menu_loop(self):
        # PCM16, 4 channels, 48 kHz, no loop despite the file name, 10 seconds.
        r = self._load("menu_loop.brstm")
        self.assertEqual(r["codec"], "pcm16")
        self.assertEqual(r["channels"], 4)
        self.assertEqual(r["sample_rate"], 48000)
        self.assertFalse(r["loop"])
        self.assertIsNone(r["loop_start"])
        self.assertEqual(r["total_samples"], 480000)
        self.assertEqual(r["duration"], "10")
        self.assertEqual(r["blocks"], 118)
        self.assertEqual(r["block_samples"], 4096)
        self.assertEqual(r["last_block_samples"], 768)
        self.assertEqual(r["expected_data_bytes"], r["data_bytes"])
        self.assertIsNone(r["adpcm"])
