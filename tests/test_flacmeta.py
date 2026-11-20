import unittest

from flacmeta import catalog, parse, probe as probe_mod
from flacmeta.blocks import FlacError, blocks


# ---------------------------------------------------------------------------
# Builders for conforming FLAC metadata byte strings (RFC 9639).
# ---------------------------------------------------------------------------

def streaminfo_body(rate=44100, channels=2, bps=16, total=1000, md5=b"\x00" * 16):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    return (
        (4096).to_bytes(2, "big")
        + (4096).to_bytes(2, "big")
        + (0).to_bytes(3, "big")
        + (0).to_bytes(3, "big")
        + packed.to_bytes(8, "big")
        + md5
    )


def block_header(kind, length, last=False):
    return bytes([(0x80 if last else 0x00) | kind]) + length.to_bytes(3, "big")


def flac(chunks, lead=b""):
    """chunks: list of (kind, body); the last chunk gets the last-block flag."""
    out = lead + b"fLaC"
    for i, (kind, body) in enumerate(chunks):
        out += block_header(kind, len(body), last=(i == len(chunks) - 1))
        out += body
    return out


def le32(n):
    return n.to_bytes(4, "little")


def be32(n):
    return n.to_bytes(4, "big")


def vorbis_block(vendor, comments):
    raw = le32(len(vendor)) + vendor
    raw += le32(len(comments))
    for name, value in comments:
        text = name + b"=" + value
        raw += le32(len(text)) + text
    return raw


def picture_block(mime=b"image/jpeg", desc=b"", width=1400, height=1400,
                  depth=24, colors=0, data=b"\xff\xd8\xff\xd9"):
    raw = be32(3)
    raw += be32(len(mime)) + mime
    raw += be32(len(desc)) + desc
    raw += be32(width) + be32(height) + be32(depth) + be32(colors) + be32(len(data))
    raw += data
    return raw


def id3v2(size, flags=0, footer=False, major=4):
    flag = flags | (0x10 if footer else 0)
    syncsafe = bytes([(size >> (7 * i)) & 0x7F for i in (3, 2, 1, 0)])
    header = b"ID3" + bytes([major, 0, flag]) + syncsafe
    body = b"\x00" * size
    foot = b"3DI" + header[3:] if footer else b""
    return header + body + foot


# ---------------------------------------------------------------------------
# Regression tests for the rejected masters.
# ---------------------------------------------------------------------------

class RejectedMasterTests(unittest.TestCase):
    def test_six_channel_24bit_streaminfo(self):
        # Case 1: 6 channels were reported as 5 and 24-bit as 23-bit.
        body = streaminfo_body(rate=192000, channels=6, bps=24, total=12345)
        info = parse.streaminfo(body)
        self.assertEqual(info["channels"], 6)
        self.assertEqual(info["bits_per_sample"], 24)
        self.assertEqual(info["sample_rate"], 192000)
        self.assertEqual(info["total_samples"], 12345)

    def test_seven_hour_high_sample_rate_total(self):
        # Case 2: 36-bit total samples must not be truncated to 32 bits.
        rate = 192000
        total = rate * 7 * 60 * 60  # 4,838,400,000, well past 2**32
        self.assertGreater(total, 0xFFFFFFFF)
        body = streaminfo_body(rate=rate, channels=2, bps=24, total=total)
        info = parse.streaminfo(body)
        self.assertEqual(info["total_samples"], total)

        data = flac([(0, body)])
        result = probe_mod.probe(data)
        self.assertEqual(result["total_samples"], total)
        self.assertEqual(result["duration"], str(total // rate))

    def test_vorbis_comment_full_block(self):
        # Case 3: little-endian framing used to make the whole block unreadable.
        body = vorbis_block(b"reference libFLAC 1.4.3",
                            [(b"TITLE", b"Harbour Lights"), (b"ISRC", b"QZDA82600001")])
        vendor, tags = parse.vorbis_comment(body)
        self.assertEqual(vendor, "reference libFLAC 1.4.3")
        self.assertEqual(tags, {"TITLE": ["Harbour Lights"], "ISRC": ["QZDA82600001"]})

    def test_duplicate_artists_kept_in_order(self):
        # Case 4: both artists must survive, in order of appearance.
        body = vorbis_block(b"v", [(b"ARTIST", b"Mira Sol"), (b"ARTIST", b"Tomas Vey")])
        _, tags = parse.vorbis_comment(body)
        self.assertEqual(tags["ARTIST"], ["Mira Sol", "Tomas Vey"])

        data = flac([(0, streaminfo_body()), (4, body)])
        rows = catalog.track_sheet([("t.flac", probe_mod.probe(data))])
        self.assertEqual(rows[0]["artists"], ["Mira Sol", "Tomas Vey"])
        self.assertEqual(rows[0]["missing"], ["TITLE", "ALBUM", "ISRC"])

    def test_leading_id3v2_is_accepted(self):
        # Case 5: an ID3v2 tag in front must not hide the FLAC stream.
        data = flac([(0, streaminfo_body())], lead=id3v2(128))
        found, audio_offset = blocks(data)
        self.assertEqual(found[0].type, 0)
        self.assertEqual(audio_offset, 10 + 128 + 4 + 4 + 34)
        self.assertEqual(probe_mod.probe(data)["channels"], 2)

    def test_leading_id3v2_with_footer(self):
        data = flac([(0, streaminfo_body())], lead=id3v2(40, footer=True))
        found, _ = blocks(data)
        self.assertEqual(found[0].type, 0)

    def test_picture_mime_has_no_garbage(self):
        # Case 6: MIME length is big-endian; the next fields must not bleed in.
        body = picture_block()
        pic = parse.picture(body)
        self.assertEqual(pic["mime"], "image/jpeg")
        self.assertEqual(pic["description"], "")
        self.assertEqual((pic["width"], pic["height"], pic["depth"]), (1400, 1400, 24))
        self.assertEqual(pic["data_length"], 4)


class StreamInfoTests(unittest.TestCase):
    def test_stored_values_minus_one_decoding(self):
        # Single channel / 8-bit uses the stored zero values.
        info = parse.streaminfo(streaminfo_body(rate=8000, channels=1, bps=8, total=7))
        self.assertEqual((info["channels"], info["bits_per_sample"]), (1, 8))
        self.assertEqual(info["total_samples"], 7)

    def test_total_samples_unknown_has_no_duration(self):
        info = probe_mod.probe(flac([(0, streaminfo_body(total=0))]))
        self.assertEqual(info["total_samples"], 0)
        self.assertIsNone(info["duration"])

    def test_invalid_streaminfo_size(self):
        with self.assertRaises(FlacError):
            parse.streaminfo(b"\x00" * 30)


class VorbisCommentTests(unittest.TestCase):
    def test_field_names_are_upper_cased(self):
        body = vorbis_block(b"v", [(b"title", b"x"), (b"AlBuM", b"y")])
        _, tags = parse.vorbis_comment(body)
        self.assertEqual(set(tags), {"TITLE", "ALBUM"})

    def test_value_may_contain_equals(self):
        body = vorbis_block(b"v", [(b"COMMENT", b"a=b=c")])
        _, tags = parse.vorbis_comment(body)
        self.assertEqual(tags["COMMENT"], ["a=b=c"])

    def test_comment_without_separator_is_an_error(self):
        raw = le32(1) + b"v" + le32(1) + le32(4) + b"nope"
        with self.assertRaises(FlacError):
            parse.vorbis_comment(raw)

    def test_big_endian_framing_is_rejected(self):
        # Big-endian vendor length is the old bug; the block is not valid now.
        raw = (4).to_bytes(4, "big") + b"vend" + le32(0)
        with self.assertRaises(FlacError):
            parse.vorbis_comment(raw)

    def test_truncated_block_is_an_error(self):
        body = vorbis_block(b"v", [(b"TITLE", b"x")])
        with self.assertRaises(FlacError):
            parse.vorbis_comment(body[:-2])


class BlockWalkingTests(unittest.TestCase):
    def test_last_block_flag_ends_chain_and_locates_audio(self):
        data = flac([(0, streaminfo_body()), (1, b"\x00" * 8)]) + b"\xff\xf8audio"
        found, audio_offset = blocks(data)
        self.assertEqual([b.type for b in found], [0, 1])
        self.assertEqual(data[audio_offset:], b"\xff\xf8audio")

    def test_unknown_block_type_is_skipped_by_length(self):
        reserved = (7, b"\x11" * 12)
        data = flac([(0, streaminfo_body()), reserved, (1, b"")])
        found, _ = blocks(data)
        self.assertEqual([b.type for b in found], [0, 7, 1])
        self.assertEqual(found[1].length, 12)

    def test_truncated_block_body_is_an_error(self):
        data = flac([(0, streaminfo_body())])[:-5]
        with self.assertRaises(FlacError):
            blocks(data)

    def test_truncated_header_is_an_error(self):
        with self.assertRaises(FlacError):
            blocks(b"fLaC\x00\x00")

    def test_type_127_is_rejected(self):
        data = b"fLaC" + block_header(127, 4) + b"\x00\x00\x00\x00"
        with self.assertRaises(FlacError):
            blocks(data)

    def test_chain_without_last_flag_is_an_error(self):
        data = b"fLaC" + block_header(0, 34) + streaminfo_body()  # last flag clear
        with self.assertRaises(FlacError):
            blocks(data)

    def test_not_flac(self):
        with self.assertRaises(FlacError):
            blocks(b"RIFF....")

    def test_id3_without_flac_is_rejected(self):
        with self.assertRaises(FlacError):
            blocks(id3v2(20) + b"RIFFdata")

    def test_id3_syncsafe_size_is_respected(self):
        # Plain 128 in a syncsafe field would be illegal; syncsafe 128 = 00 00 01 00.
        data = flac([(0, streaminfo_body())], lead=id3v2(128))
        found, _ = blocks(data)
        self.assertEqual(found[0].type, 0)


class StructuralTests(unittest.TestCase):
    def test_streaminfo_must_be_first(self):
        with self.assertRaises(FlacError):
            probe_mod.probe(flac([(1, b"")]))

    def test_duplicate_vorbis_comment_is_an_error(self):
        body = vorbis_block(b"v", [])
        data = flac([(0, streaminfo_body()), (4, body), (4, body)])
        with self.assertRaises(FlacError):
            probe_mod.probe(data)

    def test_duplicate_seektable_is_an_error(self):
        point = b"\x00" * 18
        data = flac([(0, streaminfo_body()), (3, point), (3, point)])
        with self.assertRaises(FlacError):
            probe_mod.probe(data)

    def test_seektable_wrong_size_is_an_error(self):
        with self.assertRaises(FlacError):
            parse.seektable(b"\x00" * 20)

    def test_seektable_points_decoded(self):
        raw = (1000).to_bytes(8, "big") + (44).to_bytes(8, "big") + (4096).to_bytes(2, "big")
        self.assertEqual(parse.seektable(raw), [(1000, 44, 4096)])

    def test_non_zero_padding_is_an_error(self):
        with self.assertRaises(FlacError):
            parse.padding(b"\x00\x01\x00")

    def test_padding_totaled(self):
        data = flac([(0, streaminfo_body()), (1, b"\x00" * 10), (1, b"\x00" * 6)])
        self.assertEqual(probe_mod.probe(data)["padding"], 16)

    def test_picture_data_length_mismatch_is_an_error(self):
        body = picture_block()
        with self.assertRaises(FlacError):
            parse.picture(body + b"\x00")

    def test_padding_block_does_not_swallow_audio_frames(self):
        # A declared PADDING block longer than the file must error, not drift.
        data = b"fLaC" + block_header(1, 64, last=True) + b"\x00" * 10
        with self.assertRaises(FlacError):
            blocks(data)


class CatalogTests(unittest.TestCase):
    def test_missing_tags_with_list_values(self):
        self.assertEqual(catalog.missing_tags({"TITLE": ["x"], "ARTIST": ["y"]}),
                         ["ALBUM", "ISRC"])

    def test_present_field_even_with_empty_value(self):
        tags = {"TITLE": [""], "ARTIST": ["a"], "ALBUM": ["b"], "ISRC": ["i"]}
        self.assertEqual(catalog.missing_tags(tags), [])


if __name__ == "__main__":
    unittest.main()
