import os
import unittest
from fractions import Fraction

from cdgkit.cdg import CdgError, W, H, parse

from packs import (border, colours, packet, preset, scroll_copy, scroll_preset, tile, transparent,
                   xor_tile)


class BasicTests(unittest.TestCase):
    def test_counts(self):
        out = parse(preset() + tile(1, 1, 0, 1) + bytes(24))
        self.assertEqual((out["packets"], out["graphics_packets"]), (3, 2))

    def test_tile_pixels(self):
        out = parse(preset() + tile(2, 3, 0, 5))
        self.assertEqual(out["pixels"]["5"], 72)

    def test_duration_counts_packets_not_sectors(self):
        # 300 packets is one second of subcode, whether or not they look like sectors.
        out = parse(bytes(24 * 300))
        self.assertEqual(out["duration"], "1")

    def test_high_bits_of_subcode_are_ignored(self):
        # Every ripper sets the P and Q bits differently; only the low six bits are data.
        for top2 in range(4):
            out = parse(preset(3, top2) + tile(2, 3, 0, 5, top2) + border(7, top2))
            self.assertEqual(out["graphics_packets"], 3, top2)
            self.assertEqual(out["pixels"]["5"], 72, top2)
            self.assertEqual(out["border"], 7, top2)

    def test_memory_preset_group_counts_once(self):
        out = parse(b"".join(preset(4) for _ in range(16)))
        self.assertEqual(out["clears"], ["0"])
        self.assertEqual(out["instructions"]["memory_preset"], 16)
        self.assertEqual(out["pixels"], {"4": W * H})

    def test_separate_memory_presets_count_separately(self):
        out = parse(b"".join(preset(4) for _ in range(16)) + tile(0, 0, 0, 1)
                    + b"".join(preset(6) for _ in range(16)))
        self.assertEqual(out["clears"], ["0", "17/300"])
        self.assertEqual(out["pixels"], {"6": W * H})

    def test_repeated_preset_colour_clear_counts_again(self):
        # Two full groups in a row with the same colour are still two clears.
        out = parse(b"".join(preset(4) for _ in range(32)))
        self.assertEqual(len(out["clears"]), 2)
        self.assertEqual(out["clears"][1], str(Fraction(16, 300)))

    def test_preset_repeat_counter_separates_same_colour_groups(self):
        # Some rippers carry the 0..15 repeat count in the second data byte. A counter that
        # starts over marks a new group even when the colour has not changed.
        group = b"".join(packet(1, [4, i]) for i in range(16))
        out = parse(group + group)
        self.assertEqual(out["clears"], ["0", str(Fraction(16, 300))])
        self.assertEqual(out["instructions"]["memory_preset"], 32)

    def test_preset_without_repeat_counter_groups_by_colour(self):
        # Rippers that leave the second data byte at 0 still send one group of 16 packets.
        out = parse(b"".join(preset(4) for _ in range(16)))
        self.assertEqual(out["clears"], ["0"])

    def test_colour_table_halves(self):
        low = [(i, i, i) for i in range(8)]
        high = [(i, i, i) for i in range(8)]
        out = parse(colours(False, low) + colours(True, high))
        self.assertEqual(out["colors"][0:2], ["#000000", "#111111"])
        self.assertEqual(out["colors"][7:9], ["#777777", "#000000"])
        self.assertEqual(out["colors"][15], "#777777")

    def test_colour_components_match_cdg_packing(self):
        out = parse(colours(False, [(0xF, 0x0, 0x0), (0x0, 0xF, 0x0), (0x0, 0x0, 0xF)]))
        self.assertEqual(out["colors"][:3], ["#ff0000", "#00ff00", "#0000ff"])

    def test_define_transparent(self):
        self.assertIsNone(parse(preset()).get("transparent"))
        self.assertEqual(parse(transparent(9))["transparent"], 9)

    def test_scroll_offsets(self):
        self.assertEqual(parse(scroll_preset(3, 5))["scroll_offset"], {"h": 3, "v": 5})
        self.assertEqual(parse(scroll_copy(1, 11))["scroll_offset"], {"h": 1, "v": 11})
        self.assertEqual(parse(preset())["scroll_offset"], {"h": 0, "v": 0})

    def test_xor_tile(self):
        # An XOR tile flips pixels, so drawing the same tile twice restores the screen.
        tile_packets = preset(0) + xor_tile(0, 0, 0, 5, pattern=0x0F)
        once = parse(tile_packets)
        twice = parse(tile_packets + xor_tile(0, 0, 0, 5, pattern=0x0F))
        self.assertEqual(once["pixels"]["5"], 4 * 12)         # four set bits in each of 12 rows
        self.assertEqual(twice["pixels"], {"0": W * H})

    def test_tiles_outside_screen_memory_draw_nothing(self):
        out = parse(preset() + tile(18, 0, 0, 1) + tile(0, 50, 0, 1) + tile(17, 49, 0, 1))
        self.assertEqual(out["tiles_drawn"], 1)
        self.assertEqual(out["pixels"]["1"], 6 * 12)

    def test_first_tile_is_first_drawn_tile(self):
        out = parse(bytes(24 * 300) + tile(18, 0, 0, 1) + tile(0, 0, 0, 1))
        self.assertEqual(out["first_tile"], "301/300")

    def test_pixels_cover_whole_screen_with_border(self):
        out = parse(preset(2))
        self.assertEqual(sum(out["pixels"].values()), W * H)
        self.assertEqual(out["visible_pixels"], {"2": 288 * 192})

    def test_visible_pixels_exclude_border(self):
        out = parse(preset() + tile(0, 0, 0, 1))
        self.assertEqual(sum(out["visible_pixels"].values()), 288 * 192)
        self.assertEqual(out["pixels"]["1"], 72)
        self.assertEqual(out["visible_pixels"].get("1", 0), 0)

    def test_non_graphics_packets_are_not_counted(self):
        out = parse(bytes(24) + packet(9, [0]) + bytes(24))
        self.assertEqual(out["graphics_packets"], 1)
        self.assertEqual(out["instructions"], {"type_9": 1})

    def test_partial_packet_is_rejected(self):
        with self.assertRaises(CdgError):
            parse(bytes(25))


class ExampleTests(unittest.TestCase):
    """The bundled rips set the P and Q bits one way each, as real rippers do."""

    def load(self, name):
        path = os.path.join(os.path.dirname(__file__), "..", "examples", name)
        with open(path, "rb") as fh:
            return parse(fh.read())

    def check(self, name):
        out = self.load(name)
        self.assertEqual(out["duration"], "633/100")          # 1899 packets at 300 a second
        self.assertEqual(out["graphics_packets"], 69)
        self.assertEqual(out["clears"], ["2", "1277/300"])
        self.assertEqual(out["tiles_drawn"], 34)
        self.assertEqual(out["first_tile"], "619/300")
        self.assertEqual(out["colors"][1], "#ffffff")
        self.assertEqual(sum(out["pixels"].values()), W * H)
        self.assertEqual(sum(out["visible_pixels"].values()), 288 * 192)

    def test_ballad(self):
        self.check("ballad.cdg")

    def test_duet(self):
        self.check("duet.cdg")
