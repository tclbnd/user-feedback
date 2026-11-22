import os
import unittest

from psfkit.psf import parse, seconds, PsfError

from rips import psf, psf2

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(psf(tags=[("title", "Intro"), ("game", "Quest")]))
        self.assertEqual((out["system"], out["program_bytes"], out["title"], out["game"]), ("PlayStation", 4096, "Intro", "Quest"))

    def test_plain_length(self):
        self.assertEqual(parse(psf(tags=[("length", "95")]))["length"], "95")


class SystemTests(unittest.TestCase):
    def test_version_bytes(self):
        for version, name in [(1, "PlayStation"), (2, "PlayStation 2"), (0x11, "Saturn"),
                              (0x12, "Dreamcast"), (0x21, "Nintendo 64"), (0x22, "Game Boy Advance"),
                              (0x23, "Super NES"), (0x41, "Capcom QSound")]:
            with self.subTest(version=version):
                self.assertEqual(parse(psf(version=version))["system"], name)

    def test_unknown_version(self):
        self.assertEqual(parse(psf(version=0x7F))["system"], "0x7F")


class ProgramTests(unittest.TestCase):
    def test_reserved_area_ps1(self):
        out = parse(psf(reserved=b"\x00" * 64))
        self.assertEqual((out["reserved_bytes"], out["program_bytes"], out["crc_ok"]), (64, 4096, True))

    def test_reserved_area_ps2(self):
        out = parse(psf2(reserved=b"\x00" * 16))
        self.assertEqual((out["system"], out["reserved_bytes"], out["program_bytes"], out["crc_ok"]),
                         ("PlayStation 2", 16, 4096, True))

    def test_no_program(self):
        out = parse(psf(program=b"", tags=[("title", "Rip")]))
        self.assertEqual((out["program_bytes"], out["crc_ok"]), (0, None))
        self.assertEqual(out["title"], "Rip")

    def test_bad_crc(self):
        self.assertFalse(parse(psf(crc_bad=True))["crc_ok"])

    def test_truncated_program(self):
        data = psf()[:-10]
        with self.assertRaises(PsfError):
            parse(data)


class SecondsTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(seconds("95"), "95")
        self.assertEqual(seconds("95.5"), "191/2")
        self.assertEqual(seconds("95,5"), "191/2")
        self.assertEqual(seconds("2:35.5"), "311/2")
        self.assertEqual(seconds("1:02:03"), "3723")
        self.assertEqual(seconds(" 1:05 "), "65")

    def test_unreadable(self):
        for bad in ("", "1:2:3:4", "1.5:30", "abc", ":30", "1:"):
            with self.subTest(bad=bad):
                self.assertIsNone(seconds(bad))

    def test_length_and_fade(self):
        out = parse(psf(tags=[("length", "3:20"), ("fade", "1,5")]))
        self.assertEqual((out["length"], out["fade"]), ("200", "3/2"))


class TagTests(unittest.TestCase):
    def test_case_insensitive_names_and_main_fields(self):
        out = parse(psf(tags=[("Title", "Opening"), ("ARTIST", "Composer A"), ("Game", "Legend"),
                              ("Year", "1997")]))
        self.assertEqual((out["title"], out["artist"], out["game"], out["year"]),
                         ("Opening", "Composer A", "Legend", "1997"))

    def test_trim_spaces_and_controls(self):
        out = parse(psf(raw_tag=b" title \t= Opening \n\t artist =\tA\r\n"))
        self.assertEqual((out["title"], out["artist"]), ("Opening", "A"))

    def test_repeated_name_is_multiline(self):
        out = parse(psf(tags=[("comment", "first line"), ("comment", "second line")]))
        self.assertEqual(out["tags"]["comment"], "first line\nsecond line")

    def test_crlf(self):
        out = parse(psf(tags=[("title", "Opening"), ("comment", "x"), ("comment", "y")], crlf=True))
        self.assertEqual(out["title"], "Opening")
        self.assertEqual(out["tags"]["comment"], "x\ny")

    def test_other_tags_lowercased(self):
        out = parse(psf(tags=[("PSFby", "ripper"), ("Comment", "take")]))
        self.assertEqual(out["tags"], {"psfby": "ripper", "comment": "take"})

    def test_windows_1252_default(self):
        # 0xE9 is e-acute in Windows-1252 (and latin-1), utf8 flag absent.
        out = parse(psf(raw_tag="game=Caf\xe9".encode("windows-1252")))
        self.assertEqual(out["utf8"], False)
        self.assertEqual(out["game"], "Café")

    def test_utf8_encoding(self):
        out = parse(psf(raw_tag=b"utf8=1\ntitle=" + "Café".encode("utf-8")))
        self.assertEqual(out["utf8"], True)
        self.assertEqual(out["title"], "Café")


class LibTests(unittest.TestCase):
    def test_loading_order(self):
        out = parse(psf(tags=[("_lib", "a.gsflib"), ("_lib2", "b.gsflib"), ("_lib3", "c.gsflib")]))
        self.assertEqual(out["libs"], ["a.gsflib", "b.gsflib", "c.gsflib"])

    def test_gap_stops_chain(self):
        out = parse(psf(tags=[("_lib", "a"), ("_lib3", "c")]))
        self.assertEqual(out["libs"], ["a"])
        self.assertEqual(out["tags"], {"_lib3": "c"})

    def test_library_case_insensitive(self):
        out = parse(psf(tags=[("_LIB", "a"), ("_Lib2", "b")]))
        self.assertEqual(out["libs"], ["a", "b"])


class ExampleTests(unittest.TestCase):
    def _parse(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return parse(fh.read())

    def test_opening_psf(self):
        out = self._parse("opening.psf")
        self.assertEqual(out["system"], "PlayStation")
        self.assertEqual((out["reserved_bytes"], out["compressed_bytes"], out["program_bytes"]), (0, 315, 4096))
        self.assertTrue(out["crc_ok"])
        self.assertEqual(out["title"], "Opening")
        self.assertEqual(out["artist"], "Composer A")
        self.assertEqual(out["game"], "Legend")
        self.assertEqual(out["year"], "1997")
        self.assertEqual((out["length"], out["fade"]), ("311/2", "10"))
        self.assertEqual(out["tags"]["comment"], "Recorded from the arcade board\nsecond take")
        self.assertEqual(out["tags"]["psfby"], "ripper")

    def test_minigsf_with_reserved_area(self):
        out = self._parse("stage2.minigsf")
        self.assertEqual(out["system"], "Game Boy Advance")
        self.assertEqual(out["reserved_bytes"], 64)
        self.assertEqual(out["program_bytes"], 2000)
        self.assertTrue(out["crc_ok"])
        self.assertEqual(out["title"], "Stage 2")
        self.assertEqual(out["length"], "65")
        self.assertEqual(out["libs"], ["legend.gsflib"])


if __name__ == "__main__":
    unittest.main()
