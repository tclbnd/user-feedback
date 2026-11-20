import unittest

from flacmeta import catalog, parse
from flacmeta.blocks import FlacError, blocks
from flacmeta.probe import probe

UNKNOWN36 = (1 << 36) - 1


def streaminfo_body(rate=44100, channels=2, bps=16, total=1000):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    return ((4096).to_bytes(2, "big") * 2 + bytes(6)
            + packed.to_bytes(8, "big") + bytes(16))


def block(kind, body, last=False):
    flag = 0x80 if last else 0
    return bytes([flag | kind]) + len(body).to_bytes(3, "big") + body


def flac(*chunks):
    return b"fLaC" + b"".join(chunks)


def comment_block(vendor="v", fields=(), last=False):
    body = len(vendor).to_bytes(4, "little") + vendor.encode()
    body += len(fields).to_bytes(4, "little")
    for name, value in fields:
        entry = (name + "=" + value).encode()
        body += len(entry).to_bytes(4, "little") + entry
    return block(4, body, last=last)


def picture_block(mime="image/png", desc="", w=10, h=20, depth=24, colors=0,
                  data=b"x" * 5):
    body = (3).to_bytes(4, "big")
    body += len(mime).to_bytes(4, "big") + mime.encode("ascii")
    body += len(desc).to_bytes(4, "big") + desc.encode("utf-8")
    body += w.to_bytes(4, "big") + h.to_bytes(4, "big") + depth.to_bytes(4, "big")
    body += colors.to_bytes(4, "big") + len(data).to_bytes(4, "big") + data
    return block(6, body)


class StreamInfoTests(unittest.TestCase):
    def test_stored_minus_one_fields(self):
        # six channels, 24-bit: rejected master case #1
        info = parse.streaminfo(streaminfo_body(rate=96000, channels=6,
                                                 bps=24, total=123))
        self.assertEqual(info["channels"], 6)
        self.assertEqual(info["bits_per_sample"], 24)
        self.assertEqual(info["sample_rate"], 96000)
        self.assertEqual(info["total_samples"], 123)

    def test_36bit_total_samples(self):
        # 7h live recording at 192 kHz: 4.8 million samples, past 32 bits (#2)
        total = 7 * 3600 * 192000
        info = parse.streaminfo(streaminfo_body(rate=192000, total=total))
        self.assertEqual(info["total_samples"], total)

    def test_unknown_total_is_none(self):
        info = parse.streaminfo(streaminfo_body(total=UNKNOWN36))
        self.assertIsNone(info["total_samples"])

    def test_bad_streaminfo_size(self):
        with self.assertRaises(FlacError):
            parse.streaminfo(b"\x00" * 10)


class BlockWalkTests(unittest.TestCase):
    def test_id3_prefix_is_skipped(self):
        id3 = b"ID3" + bytes([4, 0, 0]) + (0).to_bytes(4, "big")
        data = id3 + flac(block(0, streaminfo_body(), last=True))
        found, audio = blocks(data)
        self.assertEqual(found[0].type, 0)
        self.assertEqual(audio, len(data))

    def test_id3_footer_size_is_accounted_for(self):
        size = 20
        id3 = b"ID3" + bytes([4, 0, 0x10]) + size.to_bytes(4, "big")
        id3 = id3 + b"\x00" * size + b"\x00" * 10
        data = id3 + flac(block(0, streaminfo_body(), last=True))
        found, audio = blocks(data)
        self.assertEqual(audio, len(data))

    def test_garbage_prefix_rejected(self):
        with self.assertRaises(FlacError):
            blocks(b"RIFFxxxx" + flac(block(0, streaminfo_body(), last=True)))

    def test_last_flag_stops_before_audio(self):
        data = flac(block(0, streaminfo_body()),
                    block(1, b"\x00" * 8, last=True))
        data = data + b"\xff\xf8" + b"\x00" * 40  # audio must not be walked
        found, audio = blocks(data)
        self.assertEqual([blk.type for blk in found], [0, 1])
        self.assertEqual(data[audio:audio + 2], b"\xff\xf8")

    def test_truncated_block_body_errors(self):
        body = streaminfo_body()
        data = flac(bytes([0]) + (100).to_bytes(3, "big") + body)
        with self.assertRaises(FlacError):
            blocks(data)

    def test_truncated_header_errors(self):
        with self.assertRaises(FlacError):
            blocks(flac(b"\x00\x00\x00"))

    def test_unknown_block_type_errors(self):
        data = flac(block(0, streaminfo_body()),
                    bytes([0x87]) + (0).to_bytes(3, "big"))
        with self.assertRaises(FlacError):
            blocks(data)

    def test_missing_streaminfo_errors(self):
        with self.assertRaises(FlacError):
            probe(flac(comment_block(last=True)))


class VorbisCommentTests(unittest.TestCase):
    def test_little_endian_lengths(self):
        # whole block unreadable under the old big-endian parser (#3)
        data = flac(block(0, streaminfo_body()),
                    comment_block("reference libFLAC", [("TITLE", "x")]),
                    block(1, b"", last=True))
        result = probe(data)
        self.assertEqual(result["vendor"], "reference libFLAC")
        self.assertEqual(result["tags"]["TITLE"], ["x"])

    def test_duplicate_fields_keep_order_and_uppercase(self):
        data = flac(block(0, streaminfo_body()),
                    comment_block(fields=[("artist", "Mira Sol"),
                                          ("ARTIST", "Tomas Vey"),
                                          ("title", "Duet")]),
                    block(1, b"", last=True))
        tags = probe(data)["tags"]
        self.assertEqual(tags["ARTIST"], ["Mira Sol", "Tomas Vey"])  # (#4)
        self.assertEqual(tags["TITLE"], ["Duet"])

    def test_equals_only_splits_first(self):
        _, tags = parse.vorbis_comment(
            comment_block(fields=[("NOTE", "a=b=c")])[4:])
        self.assertEqual(tags["NOTE"], ["a=b=c"])

    def test_comment_without_equals_errors(self):
        bad = (4).to_bytes(4, "little") + b"nope"
        body = (1).to_bytes(4, "little") + b"v"
        body = body + (1).to_bytes(4, "little") + bad
        with self.assertRaises(FlacError):
            parse.vorbis_comment(body)

    def test_truncated_comment_errors(self):
        body = (99).to_bytes(4, "little") + b"v"
        with self.assertRaises(FlacError):
            parse.vorbis_comment(body)

    def test_invalid_utf8_errors(self):
        entry = b"TITLE=\xff\xfe"
        body = (1).to_bytes(4, "little") + b"v" + (1).to_bytes(4, "little")
        body = body + len(entry).to_bytes(4, "little") + entry
        with self.assertRaises(FlacError):
            parse.vorbis_comment(body)

    def test_duplicate_vorbis_block_errors(self):
        data = flac(block(0, streaminfo_body()), block(4, comment_block()[4:]),
                    block(4, comment_block()[4:], last=True))
        with self.assertRaises(FlacError):
            probe(data)


class PictureTests(unittest.TestCase):
    def test_big_endian_mime_and_fields(self):
        pic = parse.picture(picture_block(mime="image/jpeg",
                                          desc="cover")[4:])
        self.assertEqual(pic["mime"], "image/jpeg")  # no garbage (#6)
        self.assertEqual(pic["description"], "cover")
        self.assertEqual((pic["width"], pic["height"], pic["depth"]),
                         (10, 20, 24))
        self.assertEqual(pic["data_length"], 5)

    def test_truncated_picture_errors(self):
        with self.assertRaises(FlacError):
            parse.picture(picture_block()[4:-2])


class SeektableTests(unittest.TestCase):
    def test_round_trip_and_placeholder(self):
        body = (0xFFFFFFFFFFFFFFFF).to_bytes(8, "big") + (0).to_bytes(10, "big")
        body = body + (1000).to_bytes(8, "big")
        body = body + (4096).to_bytes(8, "big") + (4096).to_bytes(2, "big")
        points = parse.seektable(body)
        self.assertEqual(points, [(0xFFFFFFFFFFFFFFFF, 0, 0),
                                  (1000, 4096, 4096)])

    def test_ragged_seektable_errors(self):
        with self.assertRaises(FlacError):
            parse.seektable(b"\x00" * 19)


class ProbeTests(unittest.TestCase):
    def _file(self, **kw):
        return flac(block(0, streaminfo_body(**kw)),
                    block(1, b"", last=True))

    def test_unknown_total_has_no_duration(self):
        result = probe(self._file(total=UNKNOWN36))
        self.assertIsNone(result["total_samples"])
        self.assertIsNone(result["duration"])

    def test_duration_is_fraction_seconds(self):
        result = probe(self._file(rate=10, total=25))
        self.assertEqual(result["duration"], "5/2")

    def test_full_example_files(self):
        for path in ("examples/track01.flac", "examples/track02_hires.flac"):
            with open(path, "rb") as handle:
                result = probe(handle.read())
            self.assertTrue(result["sample_rate"] > 0)


class CatalogTests(unittest.TestCase):
    def test_missing_with_list_tags(self):
        tags = {"TITLE": ["x"], "ARTIST": ["y"]}
        self.assertEqual(catalog.missing_tags(tags), ["ALBUM", "ISRC"])

    def test_track_sheet_keeps_all_artists(self):
        info = {"tags": {"TITLE": ["Duet"],
                         "ARTIST": ["Mira Sol", "Tomas Vey"]},
                "duration": "210", "pictures": [{}]}
        row = catalog.track_sheet([("t.flac", info)])[0]
        self.assertEqual(row["artists"], ["Mira Sol", "Tomas Vey"])
        self.assertEqual(row["title"], ["Duet"])
        self.assertTrue(row["has_cover"])
        self.assertEqual(row["missing"], ["ALBUM", "ISRC"])

    def test_track_sheet_unknown_duration_passes_none(self):
        info = {"tags": {}, "duration": None, "pictures": []}
        row = catalog.track_sheet([("t.flac", info)])[0]
        self.assertIsNone(row["seconds"])


if __name__ == "__main__":
    unittest.main()
