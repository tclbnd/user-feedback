import unittest
import zlib

from tagread import feed
from tagread.frames import ID3V1_GENRES
from tagread.id3 import TagError, deunsynchronise, read_tag
from tagread.summary import summarise


def syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def v24_tag(frames, flags=0):
    """frames: list of (id bytes, frame flags bytes, body bytes)."""
    body = b""
    for fid, fflags, fbody in frames:
        body += fid + syncsafe(len(fbody)) + fflags + fbody
    return b"ID3\x04\x00" + bytes([flags]) + syncsafe(len(body)) + body


def v23_tag(frames, flags=0):
    body = b""
    for fid, fflags, fbody in frames:
        body += fid + len(fbody).to_bytes(4, "big") + fflags + fbody
    return b"ID3\x03\x00" + bytes([flags]) + syncsafe(len(body)) + body


def v22_tag(frames, flags=0):
    body = b""
    for fid, fbody in frames:
        body += fid + bytes([(len(fbody) >> 16) & 255,
                             (len(fbody) >> 8) & 255, len(fbody) & 255]) + fbody
    return b"ID3\x02\x00" + bytes([flags]) + syncsafe(len(body)) + body


class HeaderAndWalkingTests(unittest.TestCase):
    def test_v24_syncsafe_frame_size_long_title(self):
        # 159-byte frame: as a plain integer this would mis-walk the tag.
        title = b"\x03" + ("Episode 42: " + "班" * 70).encode("utf-8")
        self.assertGreater(len(title), 127)
        tag = v24_tag([
            (b"TIT2", b"\x00\x00", title),
            (b"TPE1", b"\x00\x00", b"\x03Mira"),
        ])
        summary = summarise(tag)
        self.assertEqual(summary["text"]["TPE1"], ["Mira"])
        self.assertEqual(len(summary["text"]["TIT2"]), 1)
        self.assertTrue(summary["text"]["TIT2"][0].startswith("Episode 42: "))

    def test_v23_plain_frame_size(self):
        tag = v23_tag([(b"TIT2", b"\x00\x00", b"\x00Pilot")])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Pilot"])

    def test_unsupported_version(self):
        with self.assertRaises(TagError):
            read_tag(b"ID3\x05\x00\x00\x00\x00\x00\x00")
        with self.assertRaises(TagError):
            read_tag(b"RIFF....")

    def test_padding_ends_walk(self):
        tag = v23_tag([(b"TIT2", b"\x00\x00", b"\x00Pilot")]) + b"\x00" * 20
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Pilot"])

    def test_truncated_body_raises(self):
        tag = v23_tag([(b"TIT2", b"\x00\x00", b"\x00Pilot")])
        with self.assertRaises(TagError):
            read_tag(tag[:-4])


class UnsynchronisationTests(unittest.TestCase):
    def test_deunsynchronise_pairs(self):
        self.assertEqual(deunsynchronise(b"\xff\x00\xfe\xff\x00\x00"),
                         b"\xff\xfe\xff\x00")

    def test_tag_level_unsynchronisation_v23(self):
        # FF followed by a non-zero byte: no FF 00 pair is ever produced, so
        # the frame header and the trailing terminator stay byte-identical.
        fbody = b"\x00ab\xffcd\x00"
        frame = b"TIT2" + len(fbody).to_bytes(4, "big") + b"\x00\x00" + fbody
        self.assertNotIn(b"\xff\x00", frame)
        synced = frame.replace(b"\xff", b"\xff\x00")
        tag = b"ID3\x03\x00\x80" + syncsafe(len(synced)) + synced
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["ab\xffcd"])

    def test_frame_level_unsynchronisation_v24(self):
        # UTF-16LE "A U+FE00": U+FE00 is bytes 00 FE, so writing the string as
        # ... FF 00 FE ... yields an FF 00 pair across the BOM/code-unit edge.
        # The writer inserts one 00; reversal must rebuild both code units and
        # the terminator exactly.
        original = b"\x01\xff\xfe" + "A︀".encode("utf-16-le") + b"\x00\x00"
        synced = original.replace(b"\xff\x00", b"\xff\x00\x00")
        self.assertEqual(deunsynchronise(synced), original)
        tag = v24_tag([(b"TIT2", b"\x00\x02", synced)])  # format bit 1
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["A︀"])


class ExtendedHeaderTests(unittest.TestCase):
    def test_v23_extended_header_skipped(self):
        # Header data after the size field: flags (2) + padding (4) = 6 bytes.
        ext = (6).to_bytes(4, "big") + b"\x00\x00" + b"\x00" * 4
        frame = b"TIT2" + (6).to_bytes(4, "big") + b"\x00\x00" + b"\x00Pilot"
        body = ext + frame
        tag = b"ID3\x03\x00\x40" + syncsafe(len(body)) + body
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Pilot"])

    def test_v24_extended_header_skipped(self):
        # Minimum 2.4 extended header: size (4) + flags (1) + padding (3).
        ext = syncsafe(8) + b"\x01" + b"\x00" * 3
        frame = b"TIT2" + syncsafe(6) + b"\x00\x00" + b"\x00Pilot"
        body = ext + frame
        tag = b"ID3\x04\x00\x40" + syncsafe(len(body)) + body
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Pilot"])


class FrameFlagTests(unittest.TestCase):
    def test_v23_grouping_and_compression(self):
        plain = b"\x00hello world"
        comp = zlib.compress(plain)
        # grouping identity byte + compressed frame with decompressed length
        grouped = b"\x07" + len(plain).to_bytes(4, "big") + comp
        tag = v23_tag([(b"TIT2", b"\x00\xa0", grouped)])  # 0x80 comp, 0x20 group
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["hello world"])

    def test_v23_encryption_skipped(self):
        tag = v23_tag([
            (b"TIT2", b"\x00\x40", b"\x01ciphertext"),
            (b"TALB", b"\x00\x00", b"\x00Record"),
        ])
        summary = summarise(tag)
        self.assertNotIn("TIT2", summary["text"])
        self.assertEqual(summary["text"]["TALB"], ["Record"])

    def test_v24_grouping_dli_compression(self):
        plain = b"\x00hello world"
        # DLI first (syncsafe length), then zlib payload
        payload = syncsafe(len(plain)) + zlib.compress(plain)
        payload = b"\x09" + payload  # grouping identity
        # flags: compression 0x20, DLI 0x04, grouping 0x40
        tag = v24_tag([(b"TIT2", b"\x00\x64", payload)])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["hello world"])

    def test_v24_encryption_skipped(self):
        tag = v24_tag([
            (b"TIT2", b"\x00\x10", b"\x01ciphertext"),
            (b"TALB", b"\x00\x00", b"\x00Record"),
        ])
        summary = summarise(tag)
        self.assertNotIn("TIT2", summary["text"])
        self.assertEqual(summary["text"]["TALB"], ["Record"])


class EncodingTests(unittest.TestCase):
    def test_utf16_le_bom(self):
        title = b"\x01\xff\xfe" + "Título".encode("utf-16-le") + b"\x00\x00"
        tag = v23_tag([(b"TIT2", b"\x00\x00", title)])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Título"])

    def test_utf16_be_bom(self):
        title = b"\x01\xfe\xff" + "Título".encode("utf-16-be") + b"\x00\x00"
        tag = v23_tag([(b"TIT2", b"\x00\x00", title)])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Título"])

    def test_utf16be_no_bom_encoding_2(self):
        title = b"\x02" + "Título".encode("utf-16-be") + b"\x00\x00"
        tag = v23_tag([(b"TIT2", b"\x00\x00", title)])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Título"])

    def test_utf8_encoding_3(self):
        tag = v24_tag([(b"TIT2", b"\x00\x00",
                        b"\x03\xe5\xa4\x9c\xe7\x8f\xad\x00")])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["夜班"])

    def test_latin1_encoding_0(self):
        tag = v23_tag([(b"TIT2", b"\x00\x00", b"\x00Caf\xe9\x00")])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Caf\xe9"])


class MultiValueTests(unittest.TestCase):
    def test_v24_null_separated_values(self):
        body = b"\x03Mira Sol\x00Tomas Vey\x00"
        tag = v24_tag([(b"TPE1", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["text"]["TPE1"],
                         ["Mira Sol", "Tomas Vey"])

    def test_v24_utf16_null_separated_values(self):
        body = b"\x01\xff\xfe" + "Mira Sol".encode("utf-16-le") + \
               b"\x00\x00" + "Tomas Vey".encode("utf-16-le") + b"\x00\x00"
        tag = v24_tag([(b"TPE1", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["text"]["TPE1"],
                         ["Mira Sol", "Tomas Vey"])

    def test_v23_utf16le_three_values_no_repeated_bom(self):
        # Only one BOM at the frame start; the 2nd and 3rd pieces have none
        # and must keep the little-endian codec from that BOM.
        body = b"\x01\xff\xfe" + b"\x00\x00".join(
            name.encode("utf-16-le") for name in ("Ana", "Ben", "Cyd")) + \
               b"\x00\x00"
        tag = v24_tag([(b"TPE1", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["text"]["TPE1"],
                         ["Ana", "Ben", "Cyd"])

    def test_v23_slash_separated_values(self):
        tag = v23_tag([(b"TPE1", b"\x00\x00", b"\x00Mira Sol/Tomas Vey\x00")])
        self.assertEqual(summarise(tag)["text"]["TPE1"],
                         ["Mira Sol", "Tomas Vey"])

    def test_multiple_frames_accumulate(self):
        tag = v24_tag([
            (b"TPE1", b"\x00\x00", b"\x03Mira\x00"),
            (b"TPE1", b"\x00\x00", b"\x03Tomas\x00"),
        ])
        self.assertEqual(summarise(tag)["text"]["TPE1"], ["Mira", "Tomas"])


class TconTests(unittest.TestCase):
    def test_numeric_reference(self):
        tag = v24_tag([(b"TCON", b"\x00\x00", b"\x03(17)\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"], ["Rock"])

    def test_unknown_numeric_reference_kept(self):
        tag = v24_tag([(b"TCON", b"\x00\x00", b"\x03(200)\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"], ["(200)"])

    def test_refinements_and_free_text_v23(self):
        tag = v23_tag([(b"TCON", b"\x00\x00",
                        b"\x00(32)(RX)Orchestral\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"],
                         ["Classical", "Remix", "Orchestral"])

    def test_cover_refinement(self):
        tag = v23_tag([(b"TCON", b"\x00\x00", b"\x00(CR)\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"], ["Cover"])

    def test_escaped_parenthesis(self):
        tag = v23_tag([(b"TCON", b"\x00\x00", b"\x00((Weird\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"], ["(Weird"])

    def test_bare_number_is_plain_text_in_24(self):
        tag = v24_tag([(b"TCON", b"\x00\x00", b"\x0313\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"], ["13"])

    def test_multiple_null_separated_genres_24(self):
        tag = v24_tag([(b"TCON", b"\x00\x00",
                        b"\x03(17)\x00(13)\x00Podcast\x00")])
        self.assertEqual(summarise(tag)["text"]["TCON"],
                         ["Rock", ID3V1_GENRES[13], "Podcast"])


class CommentAndUserTextTests(unittest.TestCase):
    def test_comment_latin1(self):
        tag = v23_tag([(b"COMM", b"\x00\x00",
                        b"\x00engshow notes\x00Recorded live.\x00")])
        self.assertEqual(summarise(tag)["comments"], [{
            "lang": "eng", "desc": "show notes", "text": "Recorded live."}])

    def test_comment_utf16le(self):
        body = b"\x01eng\xff\xfe" + "备注".encode("utf-16-le") + b"\x00\x00" + \
               "现场".encode("utf-16-le") + b"\x00\x00"
        tag = v23_tag([(b"COMM", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["comments"], [{
            "lang": "eng", "desc": "备注", "text": "现场"}])

    def test_comment_empty_description_utf16be(self):
        body = b"\x02eng\x00\x00" + "remaster".encode("utf-16-be") + b"\x00\x00"
        tag = v23_tag([(b"COMM", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["comments"][0]["desc"], "")
        self.assertEqual(summarise(tag)["comments"][0]["text"], "remaster")

    def test_txxx_latin1(self):
        tag = v23_tag([(b"TXXX", b"\x00\x00",
                        b"\x00EXPLICIT\x00yes\x00")])
        self.assertEqual(summarise(tag)["user"], {"EXPLICIT": "yes"})

    def test_txxx_utf16le_value_inherits_endianness(self):
        body = b"\x01\xff\xfe" + "EXPLICIT".encode("utf-16-le") + b"\x00\x00" + \
               "yes".encode("utf-16-le") + b"\x00\x00"
        tag = v24_tag([(b"TXXX", b"\x00\x00", body)])
        self.assertEqual(summarise(tag)["user"], {"EXPLICIT": "yes"})

    def test_feed_explicit_flag(self):
        tag = v23_tag([(b"TXXX", b"\x00\x00",
                        b"\x00EXPLICIT\x00YES\x00")])
        self.assertTrue(feed.feed_item(summarise(tag))["explicit"])


class V22Tests(unittest.TestCase):
    def test_v22_basic_text_frame_mapping(self):
        tag = v22_tag([(b"TT2", b"\x00Pilot\x00"), (b"TP1", b"\x00Mira\x00")])
        header, frames = read_tag(tag)
        self.assertEqual(header["version"], "2.2")
        ids = [f.id for f in frames]
        self.assertEqual(ids, ["TIT2", "TPE1"])
        summary = summarise(tag)
        self.assertEqual(summary["text"]["TIT2"], ["Pilot"])
        self.assertEqual(summary["text"]["TPE1"], ["Mira"])

    def test_v22_all_used_frame_mappings(self):
        tag = v22_tag([
            (b"TAL", b"\x00Album\x00"),
            (b"TCO", b"\x00(17)\x00"),
            (b"COM", b"\x00engnote\x00hello\x00"),
            (b"TXX", b"\x00EXPLICIT\x00yes\x00"),
        ])
        summary = summarise(tag)
        self.assertEqual(summary["text"]["TALB"], ["Album"])
        self.assertEqual(summary["text"]["TCON"], ["Rock"])
        self.assertEqual(summary["comments"][0]["text"], "hello")
        self.assertEqual(summary["user"]["EXPLICIT"], "yes")

    def test_v22_unknown_frame_id_passes_through(self):
        tag = v22_tag([(b"XYZ", b"\x00data\x00")])
        _, frames = read_tag(tag)
        self.assertEqual(frames[0].id, "XYZ")

    def test_v22_size_uses_three_plain_bytes(self):
        long_title = b"\x00" + b"x" * 300 + b"\x00"
        tag = v22_tag([(b"TT2", long_title), (b"TP1", b"\x00Mira\x00")])
        summary = summarise(tag)
        self.assertEqual(summary["text"]["TIT2"], ["x" * 300])
        self.assertEqual(summary["text"]["TPE1"], ["Mira"])

    def test_v22_utf16_text(self):
        body = b"\x01\xff\xfe" + "Harbour".encode("utf-16-le") + b"\x00\x00"
        tag = v22_tag([(b"TT2", body)])
        self.assertEqual(summarise(tag)["text"]["TIT2"], ["Harbour"])


class FeedIntegrationTests(unittest.TestCase):
    def test_feed_item_from_full_tag(self):
        tag = v24_tag([
            (b"TIT2", b"\x00\x00", b"\x03Episode 42\x00"),
            (b"TPE1", b"\x00\x00", b"\x03Mira Sol\x00Tomas Vey\x00"),
            (b"TALB", b"\x00\x00", b"\x03Night Shift\x00"),
            (b"TCON", b"\x00\x00", b"\x03(17)\x00"),
            (b"TXXX", b"\x00\x00", b"\x00EXPLICIT\x00true\x00"),
        ])
        item = feed.feed_item(summarise(tag))
        self.assertEqual(item, {
            "title": "Episode 42",
            "author": "Mira Sol, Tomas Vey",
            "album": "Night Shift",
            "genre": "Rock",
            "explicit": True,
        })


if __name__ == "__main__":
    unittest.main()
