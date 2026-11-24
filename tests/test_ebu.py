import unittest

from stlkit.stl import StlError, parse


def gsi(dfc=b"STL25.01", cct=b"00"):
    g = bytearray(b" " * 1024)
    g[0:3] = b"850"          # CPN
    g[3:11] = dfc            # DFC
    g[12:14] = cct           # CCT
    g[14:16] = b"09"         # LC
    g[16:20] = b"NEWS"
    g[238:243] = b"00002"
    g[243:248] = b"00001"
    g[256:264] = b"10000000"
    return bytes(g)


def tti(sn, ebn, text, tci=(0, 0, 0, 0), tco=(0, 0, 1, 0), cf=0, jc=2, vp=20):
    head = bytes([0]) + sn.to_bytes(2, "little") + bytes([ebn, 0])
    tc_ = bytes(tci) + bytes(tco)
    meta = bytes([vp, jc, cf])
    return head + tc_ + meta + text.ljust(112, b"\x8f")


class FrameRateTests(unittest.TestCase):
    def test_dfc_selects_frame_rate(self):
        block = tti(1, 0xFF, b"Hello")
        self.assertEqual(parse(gsi(dfc=b"STL25.01") + block)["frame_rate"], 25)
        self.assertEqual(parse(gsi(dfc=b"STL30.01") + block)["frame_rate"], 30)
        self.assertEqual(parse(gsi(dfc=b"STL24.01") + block)["frame_rate"], 24)

    def test_duration_uses_declared_rate(self):
        block = tti(1, 0xFF, b"Hello", tco=(0, 0, 0, 15))
        out25 = parse(gsi(dfc=b"STL25.01") + block)
        out30 = parse(gsi(dfc=b"STL30.01") + block)
        self.assertEqual(out25["subtitles"][0]["duration"], "3/5")
        self.assertEqual(out30["subtitles"][0]["duration"], "1/2")

    def test_unknown_dfc_is_an_error(self):
        with self.assertRaises(StlError):
            parse(gsi(dfc=b"STL99.99") + tti(1, 0xFF, b"Hello"))


class ExtensionBlockTests(unittest.TestCase):
    def test_split_subtitle_is_merged(self):
        first = tti(1, 0x00, b"Long text part one ", tco=(0, 0, 2, 0))
        last = tti(1, 0xFF, b"part two", tco=(0, 0, 2, 0))
        out = parse(gsi() + first + last)
        self.assertEqual(len(out["subtitles"]), 1)
        sub = out["subtitles"][0]
        self.assertEqual(sub["number"], 1)
        self.assertEqual(sub["lines"], ["Long text part one part two"])
        self.assertEqual((sub["in"], sub["out"]), ("00:00:00:00", "00:00:02:00"))

    def test_distinct_numbers_remain_distinct(self):
        out = parse(gsi() + tti(1, 0xFF, b"One") + tti(2, 0xFF, b"Two"))
        self.assertEqual([s["number"] for s in out["subtitles"]], [1, 2])


class CommentAndUserDataTests(unittest.TestCase):
    def test_comment_is_counted_not_listed(self):
        note = tti(1, 0xFF, b"Note for translator", cf=1)
        real = tti(2, 0xFF, b"On air")
        out = parse(gsi() + note + real)
        self.assertEqual(out["comments"], 1)
        self.assertEqual([s["number"] for s in out["subtitles"]], [2])

    def test_user_data_block_is_counted_not_listed(self):
        user = tti(1, 0xFE, b"\x01private")
        real = tti(2, 0xFF, b"On air")
        out = parse(gsi() + user + real)
        self.assertEqual(out["user_data_blocks"], 1)
        self.assertEqual([s["number"] for s in out["subtitles"]], [2])


class TextControlTests(unittest.TestCase):
    def test_newline_code_splits_rows(self):
        sub = parse(gsi() + tti(1, 0xFF, b"First row" + bytes([0x8A]) + b"second row"))["subtitles"][0]
        self.assertEqual(sub["lines"], ["First row", "second row"])

    def test_display_control_codes_are_removed(self):
        text = bytes([0x80]) + b"Ab" + bytes([0x81, 0x82, 0x83, 0x84, 0x85]) + b"cd"
        sub = parse(gsi() + tti(1, 0xFF, text))["subtitles"][0]
        self.assertEqual(sub["lines"], ["Abcd"])

    def test_empty_rows_are_left_out(self):
        text = bytes([0x8A]) + b"Only row" + bytes([0x8A, 0x8A])
        sub = parse(gsi() + tti(1, 0xFF, text))["subtitles"][0]
        self.assertEqual(sub["lines"], ["Only row"])


class CharacterTableTests(unittest.TestCase):
    def test_latin_french_and_german_accents(self):
        # French: 0xC2=e' 0xC3=e` 0xC1=A` ; German: 0xDF=sharp s, 0xEC=A", 0xF7=u"
        text = b"Caf" + bytes([0xC2]) + b" tr" + bytes([0xC3]) + b" " + \
            bytes([0xDF, 0xEC, 0xF7])
        sub = parse(gsi(cct=b"00") + tti(1, 0xFF, text))["subtitles"][0]
        self.assertEqual(sub["lines"], ["Café trè ßÄü"])

    def test_latin_full_table_matches_tech3264(self):
        # Spot checks across rows F/G of the EBU Latin national option table.
        cases = {0xE4: "ě", 0xF6: "Ü", 0xF7: "ü", 0xF9: "Á",
                 0xED: "ä", 0xEE: "ď", 0xA4: "¤", 0xDA: "À"}
        text = bytes(sorted(cases))
        sub = parse(gsi(cct=b"00") + tti(1, 0xFF, text))["subtitles"][0]
        self.assertEqual(sub["lines"], ["".join(cases[c] for c in sorted(cases))])

    def test_cyrillic_decoding(self):
        # П р и в е т , space м и р / М о с к в а (Tech 3264 table 4)
        text = b"\xbf\xe0\xd8\xd2\xd5\xe2, \xdc\xd8\xe0\x8a\xbc\xde\xe1\xda\xd2\xd0"
        out = parse(gsi(cct=b"01") + tti(1, 0xFF, text))
        self.assertEqual(out["character_table"], "cyrillic")
        self.assertEqual(out["subtitles"][0]["lines"], ["Привет, мир", "Москва"])

    def test_unknown_table_keeps_latin1(self):
        out = parse(gsi(cct=b"99") + tti(1, 0xFF, b"caf\xe9"))
        self.assertEqual(out["character_table"], "table_99")
        self.assertEqual(out["subtitles"][0]["lines"], ["café"])

    def test_greek_table_name(self):
        out = parse(gsi(cct=b"03") + tti(1, 0xFF, b"Logos"))
        self.assertEqual(out["character_table"], "greek")


class GsiTests(unittest.TestCase):
    def test_blank_titles_are_null(self):
        out = parse(gsi() + tti(1, 0xFF, b"Hello"))
        self.assertEqual(out["programme"], "NEWS")
        self.assertIsNone(out["episode"])

    def test_bad_signature_raises(self):
        with self.assertRaises(StlError):
            parse(b"x" * 2048)


if __name__ == "__main__":
    unittest.main()
