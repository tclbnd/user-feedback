import unittest

from icykit.icy import fields, scan

from streams import audio, body, head, meta


def chunk(data, size=1000):
    out = b""
    for i in range(0, len(data), size):
        piece = data[i:i + size]
        out += b"%x\r\n" % len(piece) + piece + b"\r\n"
    return out + b"0\r\n\r\n"


class HeaderTests(unittest.TestCase):
    def test_header_names_are_case_insensitive(self):
        out = scan(head(name="Harbour FM", br="128", ctype="audio/mpeg") + audio(10))
        self.assertEqual((out["name"], out["bitrate"], out["content_type"]), ("Harbour FM", 128, "audio/mpeg"))

    def test_first_repeated_header_wins(self):
        data = head(name="First", extra=["Icy-Name: Second"]) + audio(10)
        self.assertEqual(scan(data)["name"], "First")

    def test_folded_header_line(self):
        data = b"ICY 200 OK\r\nIcy-Name: Very\r\n  Long Name\r\n\r\n" + audio(10)
        self.assertEqual(scan(data)["name"], "Very Long Name")


class BodyTests(unittest.TestCase):
    def test_content_length_truncates_body(self):
        data = head(extra=["Content-Length: 5"]) + audio(500)
        self.assertEqual(scan(data)["audio_bytes"], 5)

    def test_chunked_body_has_no_framing_in_audio(self):
        payload = audio(300) + meta("Song") + audio(50)
        data = head(metaint=300, proto="HTTP/1.1 200 OK", extra=["Transfer-Encoding: chunked"])
        out = scan(data + chunk(payload))
        self.assertEqual(out["audio_bytes"], 350)
        self.assertEqual([t["title"] for t in out["titles"]], ["Song"])

    def test_chunk_extensions_are_ignored(self):
        payload = audio(20) + meta("A") + audio(5)
        first, rest = payload[:20], payload[20:]
        chunked = b"14;ext=1\r\n" + first + b"\r\n" + b"%x\r\n" % len(rest) + rest + b"\r\n0\r\n\r\n"
        out = scan(head(metaint=20, extra=["Transfer-Encoding: chunked"]) + chunked)
        self.assertEqual((out["audio_bytes"], [t["title"] for t in out["titles"]]), (25, ["A"]))

    def test_bare_lf_headers(self):
        data = b"ICY 200 OK\nIcy-Name: X\nIcy-Metaint: 10\n\n" + audio(10) + meta("T")
        out = scan(data)
        self.assertEqual((out["name"], out["audio_bytes"], out["titles"][0]["title"]), ("X", 10, "T"))

    def test_chunked_must_be_last_coding(self):
        payload = audio(100)
        data = head(extra=["Transfer-Encoding: gzip, chunked"])
        self.assertEqual(scan(data + chunk(payload))["audio_bytes"], 100)
        data = head(extra=["Transfer-Encoding: chunked, gzip"])
        self.assertNotEqual(scan(data + chunk(payload))["audio_bytes"], 100)


class MetadataTests(unittest.TestCase):
    def test_length_byte_counts_16_byte_units(self):
        data = head(metaint=100) + body(100, [meta("A"), meta("B", "http://x")], tail=30)
        out = scan(data)
        self.assertEqual(out["audio_bytes"], 230)
        self.assertEqual(out["metadata_blocks"], 2)
        self.assertEqual([t["at_audio_byte"] for t in out["titles"]], [100, 200])

    def test_zero_length_block_is_not_metadata(self):
        out = scan(head(metaint=100) + body(100, [meta("A"), meta(), meta(), meta("A")], tail=10))
        self.assertEqual(out["metadata_blocks"], 2)
        self.assertEqual([t["title"] for t in out["titles"]], ["A"])

    def test_repeated_title_is_not_a_new_track(self):
        out = scan(head(metaint=100) + body(100, [meta("A"), meta("A"), meta("B"), meta("B"), meta("A")]))
        self.assertEqual([t["title"] for t in out["titles"]], ["A", "B", "A"])

    def test_title_with_apostrophe_and_semicolon(self):
        out = scan(head(metaint=100) + body(100, [meta("Mira Sol - It's Late; Again", "http://u")]))
        self.assertEqual(out["titles"], [{"at_audio_byte": 100, "title": "Mira Sol - It's Late; Again", "url": "http://u"}])

    def test_latin1_fallback(self):
        raw = "StreamTitle='Café';".encode("latin-1")
        n = -(-len(raw) // 16)
        block = bytes([n]) + raw.ljust(n * 16, b"\0")
        out = scan(head(metaint=10) + audio(10) + block)
        self.assertEqual(out["titles"][0]["title"], "Café")

    def test_truncated_metadata_block(self):
        out = scan(head(metaint=10) + audio(10) + b"\x04StreamTi")
        self.assertTrue(out["truncated"])
        self.assertEqual(out["metadata_blocks"], 0)

    def test_fields_ignores_unknown_keys(self):
        self.assertEqual(fields("StreamTitle='A';Foo='B';"), {"StreamTitle": "A", "Foo": "B"})
