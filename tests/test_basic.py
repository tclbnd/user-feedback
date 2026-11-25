import struct
import unittest

from icckit.icc import IccError, parse


def bare():
    head = bytearray(132)
    struct.pack_into(">I", head, 0, 132)
    head[12:24] = b"mntrRGB XYZ "
    head[36:40] = b"acsp"
    return bytes(head)


def profile(version=0x02100000, tags=()):
    """Build a profile from (signature, tag data) pairs."""
    head = bytearray(bare())
    struct.pack_into(">I", head, 8, version)
    struct.pack_into(">I", head, 128, len(tags))
    table = bytearray()
    body = bytearray()
    offset = 132 + 12 * len(tags)
    for sig, data in tags:
        table += struct.pack(">4sII", sig, offset + len(body), len(data))
        body += data
        if len(body) % 4:
            body += bytes(4 - len(body) % 4)
    data = bytearray(head) + table + body
    struct.pack_into(">I", data, 0, len(data))
    return bytes(data)


def mluc(records):
    """records: (lang, text) pairs; en-US strings UTF-16BE, offsets relative to tag start."""
    header = bytearray(b"mluc\0\0\0\0")
    header += struct.pack(">II", len(records), 12)
    body = bytearray()
    base = 16 + 12 * len(records)
    entries = bytearray()
    for lang, value in records:
        raw = value.encode("utf-16-be")
        entries += lang.encode("ascii") + struct.pack(">II", len(raw), base + len(body))
        body += raw
    return bytes(header + entries + body)


def curv(count, values=()):
    return b"curv\0\0\0\0" + struct.pack(">I", count) + b"".join(values)


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(bare())
        self.assertEqual((out["class"], out["color_space"], out["tags"]), ("display", "RGB", 0))

    def test_not_icc(self):
        with self.assertRaises(IccError):
            parse(bytes(200))

    def test_version_v2(self):
        self.assertEqual(parse(profile(0x02100000))["version"], "2.1.0")
        self.assertEqual(parse(profile(0x02400000))["version"], "2.4.0")
        self.assertEqual(parse(profile(0x020f0000))["version"], "2.0.15")

    def test_version_v4(self):
        self.assertEqual(parse(profile(0x04300000))["version"], "4.3.0")
        self.assertEqual(parse(profile(0x040f0000))["version"], "4.0.15")

    def test_mluc_description_prefers_english(self):
        data = profile(0x04300000, [(b"desc", mluc([("deDE", "Graustufen"), ("enUS", "Gray 2.2")]))])
        self.assertEqual(parse(data)["description"], "Gray 2.2")

    def test_mluc_description_first_record_without_english(self):
        data = profile(0x04300000, [(b"desc", mluc([("frFR", "Profil"), ("deDE", "Profil DE")]))])
        self.assertEqual(parse(data)["description"], "Profil")

    def test_v2_description_and_text(self):
        desc = b"desc\0\0\0\0" + struct.pack(">I", 5) + b"Moni\0"
        cprt = b"text\0\0\0\0" + b"No copyright\0"
        out = parse(profile(0x02100000, [(b"desc", desc), (b"cprt", cprt)]))
        self.assertEqual((out["description"], out["copyright"]), ("Moni", "No copyright"))

    def test_missing_text_is_null(self):
        out = parse(bare())
        self.assertEqual((out["description"], out["copyright"]), (None, None))

    def test_chad_keeps_negative_elements(self):
        values = [1.0, 0.0, -0.5, 0.0, 1.0, 0.25, -0.25, 0.0, 1.0]
        raw = b"sf32\0\0\0\0" + b"".join(struct.pack(">i", int(round(v * 65536))) for v in values)
        out = parse(profile(0x04300000, [(b"chad", raw)]))
        self.assertEqual(out["chad"], ["1.0000", "0.0000", "-0.5000", "0.0000",
                                       "1.0000", "0.2500", "-0.2500", "0.0000", "1.0000"])

    def test_curve_identity(self):
        self.assertEqual(parse(profile(tags=[(b"kTRC", curv(0))]))["trc"]["gray"], "identity")

    def test_curve_gamma(self):
        self.assertEqual(parse(profile(tags=[(b"kTRC", curv(1, [struct.pack(">H", 0x0233)]))]))["trc"]["gray"],
                         "gamma 2.1992")

    def test_curve_table(self):
        table = b"".join(struct.pack(">H", v) for v in (0, 1000, 5000, 20000, 65535))
        self.assertEqual(parse(profile(tags=[(b"kTRC", curv(5, [table]))]))["trc"]["gray"], "table 5")

    def test_curve_para(self):
        # parametricCurveType, function type 3, parameters g, a, b, c, d
        params = [2.4, 0.9479, 0.0521, 0.0774, 0.0405]
        raw = b"para\0\0\0\0" + struct.pack(">H", 3) + bytes(2) + b"".join(
            struct.pack(">i", int(round(v * 65536))) for v in params)
        self.assertEqual(parse(profile(0x04300000, [(b"rTRC", raw)]))["trc"]["red"],
                         "para 3: 2.4000 0.9479 0.0521 0.0774 0.0405")

    def test_curve_para_negative_parameter(self):
        raw = b"para\0\0\0\0" + struct.pack(">H", 0) + bytes(2) + struct.pack(">i", -65536 * 1)
        self.assertEqual(parse(profile(0x04300000, [(b"bTRC", raw)]))["trc"]["blue"], "para 0: -1.0000")

    def test_unknown_para_type_errors(self):
        raw = b"para\0\0\0\0" + struct.pack(">H", 9) + bytes(2)
        with self.assertRaises(IccError):
            parse(profile(0x04300000, [(b"rTRC", raw)]))

    def test_examples(self):
        for path, version, desc, trc in (
                ("examples/monitor.icc", "2.1.0", "Studio Monitor D65", "gamma 2.1992"),
                ("examples/gray.icc", "4.3.0", "Gray 2.2", "table 5")):
            with open(path, "rb") as fh:
                out = parse(fh.read())
            self.assertEqual(out["version"], version)
            self.assertEqual(out["description"], desc)
            self.assertEqual(list(out["trc"].values()), [trc] * (3 if "RGB" == out["color_space"] else 1))
