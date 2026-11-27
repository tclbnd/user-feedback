"""QOI 1.0 conformance tests for qoikit.qoi.parse."""
import random
import unittest
import zlib

from qoikit.qoi import QoiError, parse

END = b"\x00\x00\x00\x00\x00\x00\x00\x01"


def qoi(w, h, chunks, channels=4, colorspace=0):
    return (b"qoif" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
            + bytes([channels, colorspace]) + chunks + END)


def crc(data):
    return "%08x" % zlib.crc32(data)


def encode(pixels, width, height, channels=4, colorspace=0):
    """Spec-faithful reference encoder; pixels are 3- or 4-tuples."""
    index = [(0, 0, 0, 0)] * 64
    prev = (0, 0, 0, 255)
    run = 0
    chunks = bytearray()

    def flush_run():
        nonlocal run
        if run:
            chunks.append(0xC0 | (run - 1))
            run = 0

    for raw in pixels:
        # 3-tuple input carries no alpha: keep the running alpha, matching
        # the spec rule that QOI_OP_RGB leaves the alpha channel untouched.
        px = raw if len(raw) == 4 else (raw[0], raw[1], raw[2], prev[3])
        if px == prev:
            run += 1
            if run == 62:
                flush_run()
            continue
        flush_run()
        pos = (px[0] * 3 + px[1] * 5 + px[2] * 7 + px[3] * 11) % 64
        if index[pos] == px:
            chunks.append(pos)
        else:
            # Even on a hash collision the new pixel is encoded normally and
            # overwrites the slot; INDEX is only valid for an exact match.
            if px[3] == prev[3]:
                dr = px[0] - prev[0]
                dg = px[1] - prev[1]
                db = px[2] - prev[2]
                if -2 <= dr <= 1 and -2 <= dg <= 1 and -2 <= db <= 1:
                    chunks.append(0x40 | ((dr + 2) << 4) | ((dg + 2) << 2) | (db + 2))
                elif -32 <= dg <= 31 and -8 <= dr - dg <= 7 and -8 <= db - dg <= 7:
                    chunks.append(0x80 | (dg + 32))
                    chunks.append(((dr - dg + 8) << 4) | (db - dg + 8))
                else:
                    chunks += bytes([0xFE, px[0], px[1], px[2]])
            else:
                chunks += bytes([0xFF, px[0], px[1], px[2], px[3]])
        index[pos] = px
        prev = px
    flush_run()

    return qoi(width, height, bytes(chunks), channels, colorspace)


def raw_bytes(pixels, channels):
    out = bytearray()
    for px in pixels:
        out.extend(px if len(px) == channels else (*px[:3],))
    return bytes(out)


class ChunkSemanticsTests(unittest.TestCase):
    def test_rgb_keeps_previous_alpha(self):
        # RGBA sets alpha 128; the following RGB must not reset it to 255.
        data = qoi(2, 1, b"\xFF\x0A\x14\x1E\x80" + b"\xFE\x28\x32\x3C")
        expected = bytes([10, 20, 30, 128, 40, 50, 60, 128])
        out = parse(data)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["ops"]["rgba"], 1)
        self.assertEqual(out["ops"]["rgb"], 1)

    def test_luma_uses_minus_eight_bias(self):
        # vg = 0 (byte 0xA0), dr-dg = -8, db-dg = -8 -> (248, 0, 248),
        # without the -8 bias this would wrongly decode as (8, 0, 8).
        data = qoi(2, 1, b"\xFE\x00\x00\x00" + bytes([0xA0, 0x00]))
        expected = bytes([0, 0, 0, 255, 248, 0, 248, 255])
        out = parse(data)
        self.assertEqual(out["crc32"], crc(expected))

    def test_luma_full_range(self):
        data = qoi(2, 1, b"\xFE\x80\x80\x80" + bytes([0xBF, 0xFF]))
        # vg = 31, dr-dg = 7, db-dg = 7 -> r,g may wrap: 128+31+7 = 166.
        expected = bytes([128, 128, 128, 255, 166, 159, 166, 255])
        self.assertEqual(parse(data)["crc32"], crc(expected))

    def test_diff_wraps_modulo_256(self):
        # From (1,1,1) a -2 delta on every channel wraps to (255,255,255).
        data = qoi(2, 1, b"\xFE\x01\x01\x01" + b"\x40")
        expected = bytes([1, 1, 1, 255, 255, 255, 255, 255])
        self.assertEqual(parse(data)["crc32"], crc(expected))

    def test_run_length_has_bias_one(self):
        # One RGB pixel plus a run of 5 (value 4 + bias 1) = six pixels.
        data = qoi(1, 6, b"\xFE\x05\x05\x05" + b"\xC4")
        expected = bytes([5, 5, 5, 255]) * 6
        out = parse(data)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["pixels"], 6)
        self.assertEqual(out["ops"]["run"], 1)

    def test_index_uses_correct_hash(self):
        # (10,11,12,255) hashes to slot 30 with the spec hash.
        pos = (10 * 3 + 11 * 5 + 12 * 7 + 255 * 11) % 64
        self.assertEqual(pos, 30)
        # Pixel 0 stores the colour (RGBA), pixel 1 reads it back by index.
        data = qoi(2, 1, b"\xFF\x0A\x0B\x0C" + bytes([255, pos]))
        expected = bytes([10, 11, 12, 255]) * 2
        out = parse(data)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["ops"]["index"], 1)

    def test_fe_and_ff_are_rgb_rgba_not_runs(self):
        data = qoi(2, 1, b"\xFE\x01\x02\x03" + b"\xFF\x04\x05\x06\x07")
        expected = bytes([1, 2, 3, 255, 4, 5, 6, 7])
        out = parse(data)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["ops"]["run"], 0)
        self.assertEqual(out["ops"]["rgb"], 1)
        self.assertEqual(out["ops"]["rgba"], 1)


class RoundtripTests(unittest.TestCase):
    def assert_roundtrips(self, pixels, w, h, channels):
        data = encode(pixels, w, h, channels)
        out = parse(data)
        expected = raw_bytes(pixels, channels)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["width"], w)
        self.assertEqual(out["height"], h)
        self.assertEqual(out["channels"], channels)
        self.assertEqual(out["pixels"], w * h)
        row = w * channels
        self.assertEqual(out["first_row"], crc(expected[:row]))
        self.assertEqual(out["last_row"], crc(expected[-row:]))
        return out

    def test_random_rgba_exercises_every_op(self):
        rng = random.Random(182)
        totals = {"rgb": 0, "rgba": 0, "index": 0, "diff": 0, "luma": 0, "run": 0}
        for w, h in ((1, 1), (7, 3), (16, 16), (33, 9)):
            pixels = []
            for i in range(w * h):
                if i > 0 and i % 7 == 0:
                    # Revisit an older, non-adjacent pixel: forces INDEX
                    # rather than a RUN (the encoder only runs repeats).
                    px = pixels[i - 3]
                elif i % 11 == 0:
                    px = (rng.randrange(256), rng.randrange(256),
                          rng.randrange(256), rng.randrange(256))
                elif i % 5 == 0:
                    px = pixels[-1]  # force runs
                else:
                    base = pixels[-1]
                    px = (
                        (base[0] + rng.randrange(-20, 21)) & 0xFF,
                        (base[1] + rng.randrange(-2, 2)) & 0xFF,
                        (base[2] + rng.randrange(-20, 21)) & 0xFF,
                        base[3],
                    )
                pixels.append(px)
            out = self.assert_roundtrips(pixels, w, h, 4)
            for k in totals:
                totals[k] += out["ops"][k]
        for op, count in totals.items():
            self.assertGreater(count, 0, "op %r never used" % op)

    def test_rgb_channels_emit_three_bytes_per_pixel(self):
        rng = random.Random(7)
        w, h = 12, 4
        pixels = [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                  for _ in range(w * h)]
        data = encode(pixels, w, h, channels=3)
        out = parse(data)
        expected = raw_bytes(pixels, 3)
        self.assertEqual(out["crc32"], crc(expected))
        self.assertEqual(out["first_row"], crc(expected[:w * 3]))
        self.assertEqual(out["last_row"], crc(expected[-w * 3:]))

    def test_linear_colorspace_label(self):
        data = encode([(1, 2, 3)], 1, 1, colorspace=1)
        self.assertEqual(parse(data)["colorspace"], "linear")

    def test_long_run_sixty_two(self):
        pixels = [(9, 9, 9, 255)] * 62
        out = self.assert_roundtrips(pixels, 62, 1, 4)
        self.assertEqual(out["ops"]["run"], 1)

    def test_index_slot_reuse_stays_correct(self):
        # Two distinct colours mapping to the same slot; the second wins
        # and a later index read must return the stored colour. A repeat
        # would be encoded as a RUN, so other pixels separate the revisits.
        b = (0, 0, 0, 255)
        pos_b = (255 * 11) % 64
        c = next((r, 0, 0, 255) for r in range(1, 256)
                 if (r * 3 + 255 * 11) % 64 == pos_b)
        self.assertNotEqual(c, b)
        other = (10, 20, 30, 255)
        pixels = [c, b, other, b, other]
        out = self.assert_roundtrips(pixels, 5, 1, 4)
        # c and b collide: b must overwrite the slot; later reads of
        # "other" and b are exact-match INDEX lookups.
        self.assertGreaterEqual(out["ops"]["index"], 2)


class MalformedInputTests(unittest.TestCase):
    def test_bad_magic(self):
        with self.assertRaises(QoiError):
            parse(b"qoix" + bytes(18))

    def test_too_short(self):
        with self.assertRaises(QoiError):
            parse(b"qoif" + bytes(10))

    def test_bad_channels(self):
        for ch in (0, 2, 5):
            with self.assertRaises(QoiError):
                parse(qoi(1, 1, b"\x00", channels=ch))

    def test_bad_colorspace(self):
        with self.assertRaises(QoiError):
            parse(qoi(1, 1, b"\x00", colorspace=2))

    def test_zero_dimensions(self):
        with self.assertRaises(QoiError):
            parse(qoi(0, 1, b""))
        with self.assertRaises(QoiError):
            parse(qoi(1, 0, b""))

    def test_data_ends_early(self):
        with self.assertRaises(QoiError):
            parse(qoi(3, 1, b"\xFE\x01\x02\x03"))

    def test_truncated_luma_payload(self):
        with self.assertRaises(QoiError):
            parse(qoi(2, 1, b"\xFE\x00\x00\x00\x80"))

    def test_truncated_rgb_payload(self):
        with self.assertRaises(QoiError):
            parse(qoi(1, 1, b"\xFE\x01\x02"))

    def test_truncated_rgba_payload(self):
        with self.assertRaises(QoiError):
            parse(qoi(1, 1, b"\xFF\x01\x02\x03"))

    def test_bad_end_marker(self):
        data = qoi(1, 1, b"\x00")[:-1] + b"\x02"
        with self.assertRaises(QoiError):
            parse(data)

    def test_run_overshoots_pixel_count(self):
        with self.assertRaises(QoiError):
            parse(qoi(2, 1, b"\xFE\x05\x05\x05\xC4"))

    def test_trailing_bytes_rejected(self):
        good = qoi(1, 1, b"\x00")
        with self.assertRaises(QoiError):
            parse(good[:-8] + b"\x00" + good[-8:])


class ExampleFilesTests(unittest.TestCase):
    def test_examples_decode(self):
        for path in ("examples/sky.qoi", "examples/sprite.qoi"):
            with open(path, "rb") as fh:
                out = parse(fh.read())
            self.assertEqual(out["pixels"], out["width"] * out["height"])
            self.assertNotIn("error", out)


if __name__ == "__main__":
    unittest.main()
