import unittest

from jpegkit.jpeg import JpegError, parse, subsampling

import files


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(files.minimal())
        self.assertEqual((out["process"], out["width"], out["height"], out["subsampling"]), ("baseline", 64, 48, "gray"))

    def test_eoi(self):
        self.assertTrue(parse(files.minimal())["eoi"])


class FrameTests(unittest.TestCase):
    def test_progressive_dimensions(self):
        data = files.jpeg(files.sof(0xC2, width=1200, height=800,
                                    comps=((1, 0x22, 0), (2, 0x11, 1), (3, 0x11, 1))))
        out = parse(data)
        self.assertEqual((out["process"], out["width"], out["height"], out["precision"]),
                         ("progressive", 1200, 800, 8))
        self.assertEqual(out["subsampling"], "4:2:0")

    def test_every_sof_process(self):
        names = ((0xC0, "baseline"), (0xC1, "extended"), (0xC2, "progressive"), (0xC3, "lossless"),
                 (0xC5, "differential_sequential"), (0xC6, "differential_progressive"),
                 (0xC7, "differential_lossless"), (0xC9, "arithmetic_extended"),
                 (0xCA, "arithmetic_progressive"), (0xCB, "arithmetic_lossless"),
                 (0xCD, "arithmetic_differential_sequential"), (0xCE, "arithmetic_differential_progressive"),
                 (0xCF, "arithmetic_differential_lossless"))
        for marker, name in names:
            with self.subTest(marker=hex(marker)):
                out = parse(files.jpeg(files.sof(marker, width=17, height=9)))
                self.assertEqual((out["process"], out["width"], out["height"]), (name, 17, 9))

    def test_layer_markers_are_not_frames(self):
        out = parse(files.jpeg(files.segment(0xC8, b"\x00" * 4), files.segment(0xCC, b"\x00" * 4)))
        self.assertIsNone(out["process"])
        self.assertIsNone(out["color_space"])

    def test_truncated_frame_header(self):
        with self.assertRaises(JpegError):
            parse(files.jpeg(files.segment(0xC0, b"\x08\x00\x30\x00\x40")))


class SubsamplingTests(unittest.TestCase):
    def comps(self, *factors):
        return [(i + 1, h, v) for i, (h, v) in enumerate(factors)]

    def test_layouts(self):
        self.assertEqual(subsampling(self.comps((1, 1))), "gray")
        self.assertEqual(subsampling(self.comps((1, 1), (1, 1), (1, 1))), "4:4:4")
        self.assertEqual(subsampling(self.comps((2, 1), (1, 1), (1, 1))), "4:2:2")
        self.assertEqual(subsampling(self.comps((1, 2), (1, 1), (1, 1))), "4:4:0")
        self.assertEqual(subsampling(self.comps((2, 2), (1, 1), (1, 1))), "4:2:0")
        self.assertEqual(subsampling(self.comps((4, 1), (1, 1), (1, 1))), "4:1:1")
        self.assertIsNone(subsampling(self.comps((4, 2), (1, 1), (1, 1))))

    def test_unknown_and_ragged(self):
        self.assertIsNone(subsampling(self.comps((3, 1), (1, 1), (1, 1))))
        self.assertIsNone(subsampling(self.comps((2, 2), (1, 2), (1, 1))))
        self.assertIsNone(subsampling(self.comps((0, 1), (1, 1), (1, 1))))


class TableTests(unittest.TestCase):
    def test_quant_tables_in_one_segment(self):
        out = parse(files.jpeg(files.sof(), files.dqt((0, 0, [1] * 64), (1, 0, [2] * 64))))
        self.assertEqual(out["quant_tables"], 2)

    def test_sixteen_bit_quant_table(self):
        out = parse(files.jpeg(files.sof(), files.dqt((0, 1, [300] * 64), (1, 0, [1] * 64))))
        self.assertEqual(out["quant_tables"], 2)

    def test_quant_tables_across_segments(self):
        out = parse(files.jpeg(files.dqt((0, 0, [1] * 64)), files.dqt((1, 0, [1] * 64))))
        self.assertEqual(out["quant_tables"], 2)

    def test_huffman_tables_in_one_segment(self):
        tables = ((0, 0, [0, 1] + [0] * 14, [0]), (1, 0, [1] + [0] * 15, [0]), (0, 1, [2] + [0] * 15, [0, 1]))
        out = parse(files.jpeg(files.sof(), files.dht(*tables)))
        self.assertEqual(out["huffman_tables"], 3)

    def test_huffman_tables_across_segments(self):
        out = parse(files.jpeg(files.dht((0, 0, [1] + [0] * 15, [0])),
                               files.dht((1, 0, [1] + [0] * 15, [0]))))
        self.assertEqual(out["huffman_tables"], 2)

    def test_truncated_tables(self):
        with self.assertRaises(JpegError):
            parse(files.jpeg(files.segment(0xC4, b"\x00" + b"\x01" + b"\x00" * 15 + b"\x00\x00")))
        with self.assertRaises(JpegError):
            parse(files.jpeg(files.segment(0xDB, b"\x00" + b"\x00" * 64 + b"\x00")))


class RestartTests(unittest.TestCase):
    def test_restart_markers_in_scan(self):
        comps = ((1, 0x11, 0), (2, 0x11, 0), (3, 0x11, 0))
        data = b"\x12\x34\xff\x00\x56\xff\xd0\x12\x34\xff\x00\x56\xff\xd1\x12\x34"
        out = parse(files.jpeg(files.sof(0xC0, comps=comps), files.dri(2), files.sos((1, 2, 3)) + data))
        self.assertEqual((out["scans"], out["restart_markers"], out["restart_interval"]), (1, 2, 2))

    def test_fill_bytes_before_restart_marker(self):
        out = parse(files.jpeg(files.sof(), files.sos() + b"\x01\x02\xff\xff\xd3\x03\x04"))
        self.assertEqual(out["restart_markers"], 1)
        self.assertTrue(out["eoi"])

    def test_stuffed_byte_is_not_a_restart_marker(self):
        out = parse(files.jpeg(files.sof(), files.sos() + b"\x01\xff\xff\xff\x00\x02"))
        self.assertEqual((out["restart_markers"], out["eoi"]), (0, True))

    def test_restart_markers_in_every_scan(self):
        one = files.sos((1,)) + b"\x01\xff\xd7\x02"
        out = parse(files.jpeg(files.sof(), one, one, one))
        self.assertEqual((out["scans"], out["restart_markers"]), (3, 3))

    def test_stuffed_ff_at_end_of_scan(self):
        data = b"\xff\xd8" + files.sof() + files.sos() + b"\x01\x02\xff\x00"
        out = parse(data)
        self.assertEqual(out["restart_markers"], 0)
        self.assertFalse(out["eoi"])


class AdobeTests(unittest.TestCase):
    def test_transforms(self):
        for code, name in ((0, "none"), (1, "ycbcr"), (2, "ycck"), (9, None)):
            with self.subTest(code=code):
                self.assertEqual(parse(files.jpeg(files.adobe(code), files.sof()))["adobe_transform"], name)

    def test_other_app14_is_ignored(self):
        out = parse(files.jpeg(files.segment(0xEE, b"XXXX" + b"\x00" * 8), files.sof()))
        self.assertIsNone(out["adobe_transform"])


class ColorSpaceTests(unittest.TestCase):
    def test_gray(self):
        self.assertEqual(parse(files.jpeg(files.sof()))["color_space"], "gray")

    def test_three_components(self):
        comps = ((1, 0x11, 0), (2, 0x11, 0), (3, 0x11, 0))
        self.assertEqual(parse(files.jpeg(files.sof(comps=comps)))["color_space"], "ycbcr")
        self.assertEqual(parse(files.jpeg(files.sof(comps=comps), files.jfif()))["color_space"], "ycbcr")

    def test_rgb_component_ids(self):
        comps = ((0x52, 0x11, 0), (0x47, 0x11, 0), (0x42, 0x11, 0))
        self.assertEqual(parse(files.jpeg(files.sof(comps=comps)))["color_space"], "rgb")

    def test_rgb_from_adobe(self):
        comps = ((1, 0x11, 0), (2, 0x11, 0), (3, 0x11, 0))
        out = parse(files.jpeg(files.adobe(0), files.sof(comps=comps)))
        self.assertEqual(out["color_space"], "rgb")

    def test_jfif_wins_over_adobe(self):
        comps = ((1, 0x11, 0), (2, 0x11, 0), (3, 0x11, 0))
        out = parse(files.jpeg(files.adobe(0), files.jfif(), files.sof(comps=comps)))
        self.assertEqual(out["color_space"], "ycbcr")

    def test_four_components(self):
        comps = ((0x43, 0x11, 0), (0x4D, 0x11, 0), (0x59, 0x11, 0), (0x4B, 0x11, 0))
        self.assertEqual(parse(files.jpeg(files.sof(comps=comps)))["color_space"], "cmyk")
        self.assertEqual(parse(files.jpeg(files.adobe(0), files.sof(comps=comps)))["color_space"], "cmyk")
        self.assertEqual(parse(files.jpeg(files.adobe(2), files.sof(comps=comps)))["color_space"], "ycck")

    def test_two_components(self):
        comps = ((1, 0x11, 0), (2, 0x11, 0))
        self.assertIsNone(parse(files.jpeg(files.sof(comps=comps)))["color_space"])


class ExifTests(unittest.TestCase):
    def test_little_endian(self):
        data = files.jpeg(files.exif("little",
                                     ifd0=[files.text(0x010F, "Canon"), files.text(0x0110, "EOS R5"),
                                           files.short(0x0112, 6)],
                                     exif_ifd=[files.text(0x9003, "2026:11:19 17:02:33")]))
        self.assertEqual(parse(data)["exif"],
                         dict(byte_order="little", make="Canon", model="EOS R5", orientation=6,
                              datetime_original="2026:11:19 17:02:33"))

    def test_big_endian(self):
        data = files.jpeg(files.exif("big",
                                     ifd0=[files.text(0x010F, "NIKON CORPORATION"),
                                           files.text(0x0110, "NIKON Z 8"), files.short(0x0112, 8, "big")],
                                     exif_ifd=[files.text(0x9003, "2026:11:19 17:02:33")]))
        out = parse(data)["exif"]
        self.assertEqual((out["byte_order"], out["make"], out["model"], out["orientation"]),
                         ("big", "NIKON CORPORATION", "NIKON Z 8", 8))
        self.assertEqual(out["datetime_original"], "2026:11:19 17:02:33")

    def test_missing_values(self):
        self.assertEqual(parse(files.jpeg(files.exif("little", ifd0=[])))["exif"],
                         dict(byte_order="little", make=None, model=None, orientation=None,
                              datetime_original=None))

    def test_missing_exif_ifd(self):
        out = parse(files.jpeg(files.exif("little", ifd0=[files.text(0x010F, "Canon")])))["exif"]
        self.assertEqual((out["make"], out["datetime_original"]), ("Canon", None))

    def test_bad_tiff_header_is_ignored(self):
        payloads = (b"Exif\0\0XX", b"Exif\0\0II\x2b\x00\x08\x00\x00\x00",
                    b"Exif\0\0ZZ\x00\x2a\x00\x00\x00\x08\x00")
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(parse(files.jpeg(files.segment(0xE1, payload)))["exif"])

    def test_header_only_tiff_has_null_values(self):
        out = parse(files.jpeg(files.segment(0xE1, b"Exif\0\0II\x2a\x00\x08\x00\x00\x00")))["exif"]
        self.assertEqual(out["byte_order"], "little")
        self.assertIsNone(out["make"])

    def test_no_exif(self):
        self.assertIsNone(parse(files.jpeg(files.jfif(), files.sof()))["exif"])
