import os
import unittest

from psfkit.psf import PsfError, parse

from rips import psf

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(psf(tags=[("title", "Intro"), ("game", "Quest")]))
        self.assertEqual((out["system"], out["program_bytes"], out["title"], out["game"]), ("PlayStation", 4096, "Intro", "Quest"))

    def test_plain_length(self):
        self.assertEqual(parse(psf(tags=[("length", "95")]))["length"], "95")


class SystemTests(unittest.TestCase):
    def test_systems(self):
        expected = {
            0x01: "PlayStation", 0x02: "PlayStation 2", 0x11: "Saturn", 0x12: "Dreamcast",
            0x13: "Mega Drive", 0x21: "Nintendo 64", 0x22: "Game Boy Advance", 0x23: "Super NES",
            0x24: "Capcom QSound",
        }
        for version, name in expected.items():
            self.assertEqual(parse(psf(version=version))["system"], name)

    def test_unknown_system(self):
        self.assertEqual(parse(psf(version=0x7F))["system"], "0x7F")

    def test_reserved_area(self):
        out = parse(psf(version=0x22, reserved=b"\0" * 0x40))
        self.assertEqual((out["reserved_bytes"], out["program_bytes"], out["crc_ok"]), (0x40, 4096, True))

    def test_psf2(self):
        out = parse(psf(version=0x02, reserved=b"\0" * 8, tags=[("title", "T")]))
        self.assertEqual((out["system"], out["reserved_bytes"], out["program_bytes"], out["crc_ok"], out["title"]),
                         ("PlayStation 2", 8, 4096, True, "T"))

    def test_no_program(self):
        out = parse(psf(program=b""))
        self.assertEqual((out["program_bytes"], out["crc_ok"]), (0, None))

    def test_no_program_with_tags(self):
        out = parse(psf(program=b"", tags=[("title", "T"), ("length", "3:20")]))
        self.assertEqual((out["program_bytes"], out["crc_ok"], out["title"], out["length"]), (0, None, "T", "200"))

    def test_crc_mismatch(self):
        self.assertFalse(parse(psf(crc_bad=True))["crc_ok"])

    def test_truncated_program(self):
        data = psf()
        with self.assertRaises(PsfError):
            parse(data[:-10])


class TagTests(unittest.TestCase):
    def test_length_formats(self):
        cases = {"95": "95", "3:20": "200", "1:02:03": "3723", "2:35.5": "311/2",
                 "0:01,25": "5/4", ":30": None, "1:2:3:4": None, "abc": None,
                 " 1:02:03.25 ": "14893/4", "1:60": "120", "": None,
                 "03:05": "185", "0:00.50": "1/2", "-1:00": None, "1:2.5.5": None}
        for text, value in cases.items():
            self.assertEqual(parse(psf(tags=[("length", text)]))["length"], value, text)

    def test_case_insensitive_names(self):
        out = parse(psf(tags=[("TITLE", "T"), ("Artist", "A"), ("Game", "G"), ("Year", "1999")]))
        self.assertEqual((out["title"], out["artist"], out["game"], out["year"]), ("T", "A", "G", "1999"))

    def test_upper_case_utf8_flag(self):
        out = parse(psf(raw_tag="UTF8=1\ntitle=Café\n".encode("utf-8")))
        self.assertEqual((out["utf8"], out["title"], out["tags"]), (True, "Café", {}))

    def test_repeated_names(self):
        out = parse(psf(tags=[("comment", "first"), ("comment", "second")]))
        self.assertEqual(out["tags"]["comment"], "first\nsecond")

    def test_padding(self):
        raw = b"\x00 title \x00=  Intro \x00\nartist\t= A \n"
        out = parse(psf(raw_tag=raw))
        self.assertEqual((out["title"], out["artist"]), ("Intro", "A"))

    def test_crlf(self):
        out = parse(psf(raw_tag=b"title=Intro\r\nArtist=A\r\n"))
        self.assertEqual((out["title"], out["artist"]), ("Intro", "A"))

    def test_utf8(self):
        out = parse(psf(raw_tag="utf8=1\ntitle=Café\n".encode("utf-8")))
        self.assertEqual((out["utf8"], out["title"], out["tags"]), (True, "Café", {}))
        self.assertNotIn("utf8", out["tags"])

    def test_windows_1252(self):
        out = parse(psf(raw_tag=b"title=Caf\xe9\n"))
        self.assertEqual((out["utf8"], out["title"]), (False, "Café"))

    def test_windows_1252_high_bytes(self):
        # 0x93/0x94 are curly quotes in Windows-1252 but control codes in Latin-1.
        out = parse(psf(raw_tag=b"title=\x93Hi\x94\n"))
        self.assertEqual(out["title"], "“Hi”")

    def test_utf8_multibyte(self):
        out = parse(psf(raw_tag="utf8=1\nartist=ゲーム\n".encode("utf-8")))
        self.assertEqual(out["artist"], "ゲーム")

    def test_libs(self):
        tags = [("_lib", "a"), ("_lib2", "b"), ("_lib3", "c"), ("_lib5", "e")]
        out = parse(psf(tags=tags))
        self.assertEqual(out["libs"], ["a", "b", "c"])
        self.assertEqual(out["tags"], {"_lib5": "e"})
        self.assertEqual(parse(psf(tags=[("_lib3", "c")]))["libs"], [])

    def test_missing_tag_section(self):
        out = parse(psf())
        self.assertEqual((out["title"], out["length"], out["fade"], out["tags"]), (None, None, None, {}))

    def test_combined_tags(self):
        raw = (b"Title=Song Name\x00\nArtist=Someone\nLENGTH=3:20\n"
               b"comment=first line\ncomment=second line\n_lib=base.psflib\n_lib2=extra.psflib\n")
        out = parse(psf(raw_tag=raw))
        self.assertEqual((out["title"], out["artist"], out["length"]), ("Song Name", "Someone", "200"))
        self.assertEqual(out["tags"]["comment"], "first line\nsecond line")
        self.assertEqual(out["libs"], ["base.psflib", "extra.psflib"])


class ExampleTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_opening(self):
        out = self.load("opening.psf")
        self.assertEqual((out["system"], out["title"], out["artist"], out["game"], out["year"]),
                         ("PlayStation", "Opening", "Composer A", "Legend", "1997"))
        self.assertEqual((out["length"], out["fade"]), ("311/2", "10"))
        self.assertEqual(out["tags"], {"comment": "Recorded from the arcade board\nsecond take", "psfby": "ripper"})

    def test_stage2(self):
        out = self.load("stage2.minigsf")
        self.assertEqual((out["system"], out["reserved_bytes"], out["program_bytes"]),
                         ("Game Boy Advance", 64, 2000))
        self.assertEqual((out["title"], out["length"], out["libs"]), ("Stage 2", "65", ["legend.gsflib"]))
