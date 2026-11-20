import os
import unittest

from y4mlab import psnr, stream
from y4mlab.probe import frame, probe


def clip(header_line, frames, frame_payloads):
    data = header_line
    for marker, payload in zip(frames, frame_payloads):
        data += marker + b"\n" + payload
    return data


class BasicTests(unittest.TestCase):
    def test_small_420(self):
        frame = bytes([100] * 16) + bytes([128] * 8)
        data = b"YUV4MPEG2 W4 H4 F25:1 Ip A1:1 C420jpeg\n" + (b"FRAME\n" + frame) * 3
        out = probe(data)
        self.assertEqual((out["frames"], out["frame_size"], out["fps"]), (3, 24, "25/1"))
        self.assertEqual(out["mean_luma"], [100.0, 100.0, 100.0])

    def test_psnr(self):
        self.assertEqual(psnr.psnr([10, 20], [10, 22]), 45.121)


class HeaderTests(unittest.TestCase):
    def test_multiple_x_params_last_wins(self):
        data = (
            b"YUV4MPEG2 W2 H2 C420jpeg "
            b"XCOLORRANGE=LIMITED XSCSS=420JPEG XCOLORRANGE=FULL\n"
        )
        params, x_params, _ = stream.header(data)
        self.assertEqual(x_params["COLORRANGE"], "FULL")
        self.assertEqual(x_params["SCSS"], "420JPEG")

    def test_color_range_full_after_other_x_params(self):
        data = (
            b"YUV4MPEG2 W2 H2 C420jpeg XYSCSS=420JPEG "
            b"XCOLORRANGE=FULL XLEN=1234\nFRAME\n" + bytes(6)
        )
        out = probe(data)
        self.assertEqual(out["color_range"], "full")

    def test_color_range_limited_and_unknown(self):
        data = b"YUV4MPEG2 W2 H2 XCOLORRANGE=LIMITED\nFRAME\n" + bytes(6)
        self.assertEqual(probe(data)["color_range"], "limited")
        data = b"YUV4MPEG2 W2 H2\nFRAME\n" + bytes(6)
        self.assertIsNone(probe(data)["color_range"])

    def test_all_header_tags_parsed(self):
        line = (
            b"YUV4MPEG2 W1920 H1080 F30000:1001 It A1:1 C420p10 "
            b"XCOLORRANGE=FULL XSCSS=420P10\n"
        )
        params, x_params, offset = stream.header(line)
        self.assertEqual(
            params,
            {"W": "1920", "H": "1080", "F": "30000:1001", "I": "t", "A": "1:1",
             "C": "420p10"},
        )
        self.assertEqual(x_params, {"COLORRANGE": "FULL", "SCSS": "420P10"})
        self.assertEqual(offset, len(line))

    def test_bad_stream(self):
        with self.assertRaises(stream.Y4mError):
            stream.header(b"not y4m at all\n")
        with self.assertRaises(stream.Y4mError):
            stream.header(b"YUV4MPEG2 H4\n")


class PlaneLayoutTests(unittest.TestCase):
    def test_odd_dimensions_420_round_up(self):
        # 33x19: chroma is ceil(33/2) x ceil(19/2) = 17x10
        self.assertEqual(
            stream.planes(33, 19, "420jpeg"),
            [(33, 19), (17, 10), (17, 10)],
        )
        payload = bytes(range(256)) * 4
        data = clip(
            b"YUV4MPEG2 W33 H19 F30000:1001 Ip A10:11 C420jpeg XYSCSS=420JPEG\n",
            [b"FRAME"] * 3,
            [payload[:967]] * 3,
        )
        out = probe(data)
        self.assertEqual(out["planes"], [[33, 19], [17, 10], [17, 10]])
        self.assertEqual(out["frame_size"], 967)
        self.assertEqual(out["frames"], 3)

    def test_sampling_422_444_411_odd_sizes(self):
        self.assertEqual(
            stream.planes(5, 3, "422"), [(5, 3), (3, 3), (3, 3)]
        )
        self.assertEqual(
            stream.planes(5, 3, "444"), [(5, 3), (5, 3), (5, 3)]
        )
        self.assertEqual(
            stream.planes(9, 5, "411"), [(9, 5), (3, 3), (3, 3)]
        )
        self.assertEqual(stream.frame_size(9, 5, "411"), 45 + 9 + 9)

    def test_mono_has_single_plane(self):
        self.assertEqual(stream.planes(4, 4, "mono"), [(4, 4)])
        self.assertEqual(stream.planes(4, 4, "mono10"), [(4, 4)])
        self.assertEqual(stream.frame_size(4, 4, "mono"), 16)
        self.assertEqual(stream.frame_size(4, 4, "mono12"), 32)
        data = b"YUV4MPEG2 W4 H4 Cmono\n" + b"FRAME\n" + bytes(16)
        out = probe(data)
        self.assertEqual(out["planes"], [[4, 4]])
        self.assertEqual(out["frame_size"], 16)

    def test_alpha_plane_counts_and_sizes(self):
        self.assertEqual(
            stream.planes(4, 4, "444alpha"),
            [(4, 4), (4, 4), (4, 4), (4, 4)],
        )
        self.assertEqual(stream.frame_size(4, 4, "444alpha"), 64)
        self.assertEqual(
            stream.planes(6, 4, "422p10alpha"),
            [(6, 4), (3, 4), (3, 4), (6, 4)],
        )
        # 10-bit: every sample is a little-endian 16-bit word
        self.assertEqual(stream.frame_size(6, 4, "422p10alpha"), (24 + 12 + 12 + 24) * 2)
        self.assertEqual(
            stream.planes(4, 4, "420alpha"),
            [(4, 4), (2, 2), (2, 2), (4, 4)],
        )

    def test_bit_depths(self):
        for tag, bits in [
            ("420jpeg", 8), ("422p9", 9), ("420p10", 10), ("444p12", 12),
            ("422p14", 14), ("mono16", 16), ("444p10alpha", 10),
        ]:
            self.assertEqual(stream.bit_depth(tag), bits, tag)
            self.assertEqual(stream.sample_bytes(tag), 1 if bits == 8 else 2, tag)

    def test_10bit_frame_size(self):
        # 16x8 422p10: luma 128 + chroma 64 + 64 samples, 2 bytes each
        self.assertEqual(stream.frame_size(16, 8, "422p10"), 512)


class FrameLineTests(unittest.TestCase):
    def _clip(self, markers):
        # 4x4 420 -> 24 bytes per frame
        payload = bytes(24)
        header = b"YUV4MPEG2 W4 H4 C420jpeg\n"
        return header + b"".join(m + b"\n" + payload for m in markers)

    def test_frame_lines_with_params(self):
        data = self._clip([
            b"FRAME",
            b"FRAME I T1 S(0,0) R(0,0)",
            b"FRAME\tI".replace(b"\t", b" "),
        ])
        fs = stream.frames(data, len(b"YUV4MPEG2 W4 H4 C420jpeg\n"), 4, 4, "420jpeg")
        self.assertEqual(len(fs), 3)
        self.assertTrue(all(len(f) == 24 for f in fs))
        self.assertEqual(probe(data)["frames"], 3)

    def test_extract_with_param_frame_lines(self):
        markers = [b"FRAME", b"FRAME I T2 X(1,2)", b"FRAME"]
        data = self._clip(markers)
        raw = frame(data, 1)
        self.assertEqual(len(raw), 24)
        self.assertEqual(raw, bytes(24))
        with self.assertRaises(stream.Y4mError):
            frame(data, 3)

    def test_bad_frame_marker(self):
        data = b"YUV4MPEG2 W2 H2\nFREAM\n" + bytes(6)
        with self.assertRaises(stream.Y4mError):
            probe(data)

    def test_truncated_frame(self):
        data = b"YUV4MPEG2 W4 H4 C420jpeg\nFRAME\n" + bytes(10)
        with self.assertRaises(stream.Y4mError):
            probe(data)


class LumaMeanTests(unittest.TestCase):
    def test_8bit_mean_in_sample_units(self):
        payload = bytes([10, 20, 30, 40]) + bytes([0]) * 2
        data = b"YUV4MPEG2 W2 H2 C420jpeg\nFRAME\n" + payload
        self.assertEqual(probe(data)["mean_luma"], [25.0])

    def test_10bit_mean_in_sample_units(self):
        # values 0..1023 stored as little-endian 16-bit words; the whole
        # frame is 10-bit, so chroma samples are 16-bit words as well
        values = [0, 256, 512, 1023]
        luma = b"".join(v.to_bytes(2, "little") for v in values)
        # 2x2 420p10: 4 luma words + 1x1 chroma planes (2 words) = 12 bytes
        payload = luma + bytes(4)
        data = b"YUV4MPEG2 W2 H2 C420p10\nFRAME\n" + payload
        out = probe(data)
        self.assertEqual(out["bit_depth"], 10)
        self.assertEqual(out["mean_luma"], [round(sum(values) / 4, 3)])
        self.assertGreater(out["mean_luma"][0], 255)


class ExampleFileTests(unittest.TestCase):
    here = os.path.dirname(__file__)
    examples = os.path.join(here, "..", "examples")

    def test_bars_odd_file(self):
        with open(os.path.join(self.examples, "bars_odd.y4m"), "rb") as fh:
            out = probe(fh.read())
        self.assertEqual((out["width"], out["height"]), (33, 19))
        self.assertEqual(out["planes"], [[33, 19], [17, 10], [17, 10]])
        self.assertEqual(out["frame_size"], 967)
        self.assertEqual(out["frames"], 12)
        self.assertIsNone(out["color_range"])

    def test_ramp_10bit_file(self):
        with open(os.path.join(self.examples, "ramp_10bit.y4m"), "rb") as fh:
            data = fh.read()
        out = probe(data)
        self.assertEqual(out["bit_depth"], 10)
        self.assertEqual(out["colorspace"], "422p10")
        self.assertEqual(out["frame_size"], 512)
        self.assertEqual(out["frames"], 6)
        # per-frame ramps 64+8*f .. 514+8*f in 10-bit sample units
        self.assertEqual(out["mean_luma"], [289.0, 297.0, 305.0, 313.0, 321.0, 329.0])
        raw = frame(data, 5)
        self.assertEqual(len(raw), 512)


if __name__ == "__main__":
    unittest.main()
