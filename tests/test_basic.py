import os
import unittest

from ga20kit.voices import CLOCK, Ga20Error, parse

MAGIC = b"GA20SNAP"


def voice_regs(start, end, rate=0, volume=0, control=0):
    lo, hi = start & 0xFF, start >> 8
    elo, ehi = end & 0xFF, end >> 8
    return bytes((lo, hi, elo, ehi, rate, volume, control, 0))


def snapshot(voice_blocks, rom=b""):
    regs = b"".join(voice_blocks)
    regs += bytes(32 - len(regs))
    return MAGIC + regs + rom


class BasicTests(unittest.TestCase):
    def test_all_voices_off(self):
        out = parse(MAGIC + bytes(32) + bytes(256))
        self.assertEqual(out["voices_on"], 0)
        self.assertEqual(len(out["voices"]), 4)

    def test_not_a_snapshot(self):
        with self.assertRaises(Ga20Error):
            parse(b"GA20SNAX" + bytes(64))

    def test_start_end_are_paragraph_addresses(self):
        # register words 0x0040 / 0x0048 address bytes 0x400 / 0x480
        rom = bytes(0x500)
        out = parse(snapshot([voice_regs(0x0040, 0x0048, control=2)], rom))
        v = out["voices"][0]
        self.assertEqual(v["start"], 0x400)
        self.assertEqual(v["end"], 0x480)
        self.assertEqual(v["length"], 0x80)

    def test_control_nonzero_starts_voice(self):
        for control in (1, 2, 0x80, 0xFF):
            out = parse(snapshot([voice_regs(0, 1, control=control)]))
            self.assertTrue(out["voices"][0]["on"], control)
        # control == 0 leaves the voice stopped even if other regs are set
        out = parse(snapshot([voice_regs(0, 1, volume=200, control=0)]))
        self.assertFalse(out["voices"][0]["on"])
        self.assertEqual(out["voices_on"], 0)

    def test_volume_is_scaled_to_64_steps(self):
        out = parse(snapshot([voice_regs(0, 1, volume=200, control=1)]))
        self.assertEqual(out["voices"][0]["volume"], 50)
        out = parse(snapshot([voice_regs(0, 1, volume=255)]))
        self.assertEqual(out["voices"][0]["volume"], 63)

    def test_rate_uses_clock_over_four(self):
        out = parse(snapshot([voice_regs(0, 1, rate=208, control=1)]))
        expected = "%.2f" % (CLOCK / (4 * (256 - 208)))
        self.assertEqual(out["voices"][0]["rate_hz"], expected)
        # rate register 255 still divides (step 1), no None result
        out = parse(snapshot([voice_regs(0, 1, rate=255)]))
        self.assertEqual(out["voices"][0]["rate_hz"],
                         "%.2f" % (CLOCK / 4))

    def test_samples_are_unsigned_centred_at_128(self):
        # four paragraphs whose only non-silence bytes spell 0, -1, +1, -128
        rom = bytearray([0x80]) * 0x40
        rom[0x00] = 0x80  # 0
        rom[0x10] = 0x7F  # -1
        rom[0x20] = 0x81  # +1
        rom[0x30] = 0x00  # -128
        out = parse(snapshot([voice_regs(0, 4, control=1)], bytes(rom)))
        v = out["voices"][0]
        self.assertEqual(v["length"], 0x40)
        self.assertEqual(v["peak"], 128)
        self.assertEqual(v["sum"], 0 - 1 + 1 - 128)

    def test_termination_flag(self):
        rom = bytes(0x100)
        # end lands inside the ROM
        inside = parse(snapshot([voice_regs(0, 1, control=1)], rom))
        self.assertTrue(inside["voices"][0]["terminated"])
        # end lands exactly at the ROM size: the last byte is still played
        exact = parse(snapshot(
            [voice_regs(0, 0x100 >> 4, control=1)], rom))
        self.assertTrue(exact["voices"][0]["terminated"])
        self.assertEqual(exact["voices"][0]["length"], 0x100)
        # end runs past the ROM: scan stops at the end of the file
        over = parse(snapshot(
            [voice_regs(0, 0x1000 >> 4, control=1)], rom))
        self.assertFalse(over["voices"][0]["terminated"])
        self.assertEqual(over["voices"][0]["length"], 0x100)


class FixtureTests(unittest.TestCase):
    """The bundled snapshots freeze voices that are really playing."""

    def _parse(self, name):
        path = os.path.join(os.path.dirname(__file__), "..", "examples", name)
        with open(os.path.abspath(path), "rb") as fh:
            return parse(fh.read())

    def test_voice_snapshot(self):
        out = self._parse("voice.ga20")
        self.assertEqual(out["rom_size"], 65536)
        self.assertEqual(out["voices_on"], 1)
        v = out["voices"][0]
        self.assertTrue(v["on"])
        # 0x0120 / 0x0200 register words -> 0x1200 / 0x2000 byte addresses
        self.assertEqual((v["start"], v["end"]), (0x1200, 0x2000))
        self.assertEqual(v["length"], 0x2000 - 0x1200)
        self.assertEqual(v["volume"], 50)       # 200 >> 2
        self.assertEqual(v["rate_hz"],
                         "%.2f" % (CLOCK / (4 * (256 - 180))))
        self.assertTrue(v["terminated"])
        # unsigned PCM: bytes above 0x80 stay positive (peak 128 exists)
        self.assertEqual(v["peak"], 128)
        self.assertEqual(sum(1 for w in out["voices"][1:] if w["on"]), 0)
        # independent recomputation straight from the ROM bytes
        with open(os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "examples",
                "voice.ga20")), "rb") as fh:
            blob = fh.read()
        signed = [b - 128 for b in blob[40 + v["start"]:40 + v["end"]]]
        self.assertEqual(v["length"], len(signed))
        self.assertEqual(v["peak"], max(abs(x) for x in signed))
        self.assertEqual(v["sum"], sum(signed))

    def test_blast_snapshot(self):
        out = self._parse("blast.ga20")
        self.assertEqual(out["voices_on"], 2)
        v1, v2 = out["voices"][0], out["voices"][1]
        self.assertTrue(v1["on"] and v2["on"])
        self.assertEqual((v1["start"], v1["end"]), (0x400, 0x480))
        self.assertEqual((v2["start"], v2["end"]), (0x800, 0x900))
        self.assertEqual((v1["volume"], v2["volume"]), (30, 15))
        self.assertEqual(v1["rate_hz"],
                         "%.2f" % (CLOCK / (4 * (256 - 208))))
        self.assertEqual(v2["rate_hz"],
                         "%.2f" % (CLOCK / (4 * (256 - 160))))
        self.assertTrue(v1["terminated"] and v2["terminated"])


if __name__ == "__main__":
    unittest.main()

