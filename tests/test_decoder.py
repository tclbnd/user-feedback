"""Unit tests for the streaming decoder: UTF-8, cross-chunk lines, residuals."""

import unittest

from ndjson_pipe.decoder import NDJsonStreamDecoder, decode_payload


class DecoderTests(unittest.TestCase):
    def _feed_in_chunks(self, data, sizes):
        dec = NDJsonStreamDecoder()
        out = []
        pos = 0
        for size in sizes:
            out.extend(dec.feed(data[pos:pos + size]))
            pos += size
        out.extend(dec.flush(sealed=True))
        return out

    def test_basic_offsets_and_content(self):
        data = b'{"a":1}\n{"b":2}\n'
        lines = self._feed_in_chunks(data, [len(data)])
        self.assertEqual([ln.start_offset for ln in lines], [0, 8])
        self.assertEqual([ln.end_offset for ln in lines], [8, 16])
        self.assertEqual([ln.line_no for ln in lines], [1, 2])
        self.assertEqual(lines[0].raw, b'{"a":1}')

    def test_single_byte_chunks(self):
        data = '{"note":"蓝莓🫐"}\n{"x":1}\n'.encode("utf-8")
        lines = self._feed_in_chunks(data, [1] * len(data))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].raw.decode("utf-8"), '{"note":"蓝莓🫐"}')
        self.assertEqual(lines[1].raw, b'{"x":1}')
        # offsets are byte offsets, not character offsets
        self.assertEqual(lines[1].start_offset, len('{"note":"蓝莓🫐"}\n'.encode("utf-8")))

    def test_multibyte_char_split_across_chunks(self):
        # U+1FAD0 (blueberry) is 4 UTF-8 bytes; split right through it
        prefix = '{"note":"'.encode("utf-8")
        emoji = "🫐".encode("utf-8")
        suffix = '"}\n'.encode("utf-8")
        data = prefix + emoji + suffix
        cut = len(prefix) + 2
        lines = self._feed_in_chunks(data, [cut, len(data) - cut])
        self.assertEqual(len(lines), 1)
        self.assertEqual(decode_payload(lines[0].raw), '{"note":"🫐"}')

    def test_residual_line_retained_until_sealed(self):
        dec = NDJsonStreamDecoder()
        first = list(dec.feed(b'{"a":1}\n{"partial"'))
        self.assertEqual(len(first), 1)
        self.assertEqual(dec.buffered_bytes, len(b'{"partial"'))
        self.assertEqual(dec.position, len(b'{"a":1}\n'))

        # unsealed flush must not emit the residual
        self.assertEqual(list(dec.flush(sealed=False)), [])
        # a later append completes the line
        more = list(dec.feed(b':2}\n'))
        self.assertEqual(len(more), 1)
        self.assertEqual(more[0].raw, b'{"partial":2}')
        self.assertEqual(more[0].start_offset, len(b'{"a":1}\n'))

    def test_sealed_flush_emits_unterminated_last_line(self):
        dec = NDJsonStreamDecoder()
        list(dec.feed(b'{"a":1}\n{"last":true}'))
        final = list(dec.flush(sealed=True))
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].raw, b'{"last":true}')
        self.assertEqual(final[0].end_offset, len(b'{"a":1}\n{"last":true}'))

    def test_append_sequence_matches_single_pass(self):
        data = '{"note":"蓝莓"}\n{"v":2}\n{"tail":1}'.encode("utf-8")
        one_shot = self._feed_in_chunks(data, [len(data)])
        dec = NDJsonStreamDecoder()
        appended = []
        pos = 0
        for size in (3, 7, 5, len(data) - 15):
            appended.extend(dec.feed(data[pos:pos + size]))
            pos += size
        appended.extend(dec.flush(sealed=True))
        self.assertEqual(
            [(l.line_no, l.start_offset, l.end_offset, l.raw) for l in one_shot],
            [(l.line_no, l.start_offset, l.end_offset, l.raw) for l in appended],
        )

    def test_crlf_and_blank_lines(self):
        data = b'{"a":1}\r\n\r\n{"b":2}\n'
        sizes = [4, 4, 4, 4, len(data) - 16]
        lines = self._feed_in_chunks(data, sizes)
        self.assertEqual([l.raw for l in lines], [b'{"a":1}', b'', b'{"b":2}'])
        self.assertEqual(lines[1].start_offset, lines[0].end_offset)
        self.assertEqual(lines[2].start_offset, lines[1].end_offset)

    def test_resume_from_offset(self):
        data = b'{"a":1}\n{"b":2}\n'
        dec = NDJsonStreamDecoder(start_offset=8)
        lines = list(dec.feed(data[8:]))
        self.assertEqual(lines[0].start_offset, 8)
        self.assertEqual(lines[0].end_offset, 16)
        self.assertEqual(dec.position, 16)

    def test_empty_feed(self):
        dec = NDJsonStreamDecoder()
        self.assertEqual(list(dec.feed(b"")), [])
        self.assertEqual(list(dec.flush(sealed=True)), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
