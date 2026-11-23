import unittest

from mlpkit.mlp import parse

from units import SYNC_MLP, ac3, unit

# TrueHD info words: rate bits [31:28], 6ch arrangement [19:15], 8ch arrangement [12:0]
THD_48K_3CH = 0x0001E000
THD_48K_7P1 = 0x0007804F   # 6ch: 0x0F, 8ch: 0x04F
THD_96K_7P1 = 0x1007804F
THD_48K_5P1 = 0x00078000   # 6ch: 0x0F, no 8ch presentation
# MLP info word: group bits [31:24], rates [23:16], arrangement [4:0]
MLP_88K_6CH = (2 << 28) | (2 << 24) | (9 << 20) | (0xF << 16) | 12


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(unit(60, True) + unit(60))
        self.assertEqual((out["format"], out["sample_rate"], out["substreams"]), ("TrueHD", 48000, 2))

    def test_garbage_before_sync(self):
        self.assertEqual(parse(bytes(5) + unit(60, True))["skipped_bytes"], 5)

    def test_access_unit_length_is_in_words(self):
        out = parse(unit(60, True) + unit(60) + unit(40))
        self.assertEqual((out["access_units"], out["major_syncs"], out["largest_unit"]), (3, 1, 120))

    def test_samples_scale_with_rate(self):
        # 96 kHz: 80 samples per access unit, not 40
        out = parse(unit(60, True, info=THD_96K_7P1) + unit(60))
        self.assertEqual((out["sample_rate"], out["samples"], out["duration"]), (96000, 160, "1/600"))

    def test_8ch_presentation(self):
        out = parse(unit(60, True, info=THD_48K_7P1))
        self.assertEqual((out["channels"], out["channels_6ch"]), (8, 6))

    def test_6ch_only_presentation(self):
        out = parse(unit(60, True, info=THD_48K_5P1))
        self.assertEqual((out["channels"], out["channels_6ch"]), (6, 6))

    def test_mlp_stream(self):
        out = parse(unit(100, True, word=SYNC_MLP, info=MLP_88K_6CH) + unit(100))
        self.assertEqual((out["format"], out["sample_rate"], out["bits"], out["channels"]),
                         ("MLP", 88200, [24, 24], 6))
        self.assertEqual((out["access_units"], out["samples"]), (2, 160))

    def test_ac3_core_frames(self):
        out = parse(unit(60, True) + ac3() + unit(60) + ac3())
        self.assertEqual((out["access_units"], out["ac3_frames"], out["ac3_bytes"], out["ac3_kbps"]),
                         (2, 2, 1536, 192))

    def test_no_ac3(self):
        out = parse(unit(60, True))
        self.assertEqual((out["ac3_frames"], out["ac3_bytes"], out["ac3_kbps"]), (0, 0, None))

    def test_format_changes(self):
        out = parse(unit(60, True, info=THD_48K_3CH) + unit(60, True, info=THD_96K_7P1)
                    + unit(60, True, info=THD_96K_7P1))
        self.assertEqual((out["access_units"], out["major_syncs"], out["format_changes"]), (3, 3, 2))
        self.assertEqual(out["samples"], 40 + 80 + 80)
