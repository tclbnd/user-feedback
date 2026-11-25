import struct
import unittest

from anikit.ani import parse


def minimal():
    anih = b"anih" + struct.pack("<I", 36) + struct.pack("<9I", 36, 0, 0, 0, 0, 0, 0, 6, 1)
    return b"RIFF" + struct.pack("<I", 4 + len(anih)) + b"ACON" + anih


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(minimal())
        self.assertEqual((out["frames"], out["default_rate"], out["icon_frames"]), (0, 6, True))

    def test_no_frames(self):
        self.assertEqual(parse(minimal())["frame_images"], [])


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def anih(frames=1, steps=1, disp=6, attr=1):
    return chunk(b"anih", struct.pack("<9I", 36, frames, steps, 0, 0, 0, 0, disp, attr))


def ico(kind=1, images=1, w=16, h=16, hotspot=None):
    # In a CUR entry the two header fields hold the hotspot x and y.
    planes, bpp = hotspot if kind == 2 and hotspot else ((1, 32) if kind == 1 else (0, 0))
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, planes, bpp, 40, 22)
    return struct.pack("<HHH", 0, kind, images) + entry + b"\0" * 16


def png(w=256, h=256, depth=8, color=6):
    ihdr = struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", 0)


def ani(*parts, **header):
    body = b"ACON" + anih(**header) + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def framed(*bodies):
    return chunk(b"LIST", b"fram" + b"".join(chunk(b"icon", b) for b in bodies))


class RateTests(unittest.TestCase):
    def test_per_step_rates_in_sixtieths(self):
        out = parse(ani(chunk(b"rate", struct.pack("<4I", 30, 6, 6, 6)), steps=4))
        self.assertEqual(out["loop_seconds"], "4/5")  # 48/60

    def test_short_rate_chunk_uses_header_rate(self):
        out = parse(ani(chunk(b"rate", struct.pack("<I", 30)), steps=3))
        self.assertEqual(out["default_rate"], 6)
        self.assertEqual(out["loop_seconds"], "7/10")  # 42/60

    def test_extra_rates_are_ignored(self):
        out = parse(ani(chunk(b"rate", struct.pack("<4I", 30, 30, 30, 30)), steps=2))
        self.assertEqual(out["loop_seconds"], "1")  # 60/60
        self.assertEqual(len(out["sequence"]), 2)

    def test_no_rate_chunk(self):
        self.assertEqual(parse(ani())["loop_seconds"], "1/10")  # 6/60


class SequenceTests(unittest.TestCase):
    def test_seq_sets_playback_order(self):
        out = parse(ani(chunk(b"seq ", struct.pack("<5I", 0, 1, 2, 1, 0)), frames=3, steps=5))
        self.assertEqual((out["frames"], out["steps"], out["sequence"]), (3, 5, [0, 1, 2, 1, 0]))

    def test_seq_longer_than_header_steps(self):
        out = parse(ani(chunk(b"seq ", struct.pack("<3I", 2, 0, 1)), frames=3, steps=2))
        self.assertEqual(out["sequence"], [2, 0])

    def test_no_seq_plays_in_order(self):
        self.assertEqual(parse(ani(frames=4, steps=3))["sequence"], [0, 1, 2])


class InfoTests(unittest.TestCase):
    def test_title_and_author(self):
        info = chunk(b"LIST", b"INFO" + chunk(b"INAM", b"Busy Ring\0") + chunk(b"IART", b"Studio\0"))
        out = parse(ani(info))
        self.assertEqual((out["title"], out["artist"]), ("Busy Ring", "Studio"))

    def test_missing_info(self):
        out = parse(ani())
        self.assertEqual((out["title"], out["artist"]), (None, None))

    def test_absent_author(self):
        out = parse(ani(chunk(b"LIST", b"INFO" + chunk(b"INAM", b"Only Title\0"))))
        self.assertEqual((out["title"], out["artist"]), ("Only Title", None))


class FrameTests(unittest.TestCase):
    def test_cursor_hotspot(self):
        frame = parse(ani(framed(ico(kind=2, hotspot=(7, 3)))))["frame_images"][0]
        self.assertEqual((frame["type"], frame["hotspot"]), ("cursor", [7, 3]))

    def test_icon_has_no_hotspot(self):
        frame = parse(ani(framed(ico())))["frame_images"][0]
        self.assertEqual((frame["type"], frame["hotspot"]), ("icon", None))

    def test_zero_size_is_256(self):
        frame = parse(ani(framed(ico(w=0, h=0))))["frame_images"][0]
        self.assertEqual((frame["width"], frame["height"]), (256, 256))

    def test_png_image_in_cursor(self):
        head = struct.pack("<HHH", 0, 2, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 5, 0, 40, 22)
        frame = parse(ani(framed(head + png())))["frame_images"][0]
        self.assertEqual((frame["png"], frame["width"], frame["height"]), (True, 256, 256))
        self.assertEqual((frame["type"], frame["hotspot"], frame["bit_count"]), ("cursor", [5, 0], 32))

    def test_image_count(self):
        frame = parse(ani(framed(ico(images=3))))["frame_images"][0]
        self.assertEqual(frame["images"], 3)

    def test_png_size_beats_entry(self):
        head = struct.pack("<HHH", 0, 2, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 1, 40, 22)
        frame = parse(ani(framed(head + png(48, 64))))["frame_images"][0]
        self.assertEqual((frame["width"], frame["height"]), (48, 64))

    def test_bitmap_frame_bits(self):
        body = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 40, 22)
        body += struct.pack("<IiiHHIIiiII", 40, 16, 32, 1, 32, 0, 0, 0, 0, 0, 0)
        frame = parse(ani(framed(body)))["frame_images"][0]
        self.assertEqual((frame["bit_count"], frame["png"], frame["width"]), (32, False, 16))

    def test_unknown_resource_is_skipped(self):
        out = parse(ani(framed(struct.pack("<HHH", 0, 7, 1) + b"\0" * 20, ico())))
        self.assertEqual(len(out["frame_images"]), 1)


class PaddingTests(unittest.TestCase):
    def test_odd_chunk_is_skipped_whole(self):
        out = parse(ani(chunk(b"junk", b"abc"), chunk(b"rate", struct.pack("<I", 30)), steps=1,
                        *[framed(ico()), framed(ico())]))
        self.assertEqual((len(out["frame_images"]), out["loop_seconds"]), (2, "1/2"))

    def test_padded_seq(self):
        out = parse(ani(chunk(b"junk", b"abc"), chunk(b"seq ", struct.pack("<3I", 1, 0, 1)), steps=3))
        self.assertEqual(out["sequence"], [1, 0, 1])
