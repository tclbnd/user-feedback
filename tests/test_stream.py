import unittest

from pipeline.stream import ByteLineSplitter


class ByteLineSplitterTest(unittest.TestCase):
    def test_lines_across_chunks(self):
        sp = ByteLineSplitter()
        self.assertEqual(sp.feed(b'{"a":1}\n{"b":'), [b'{"a":1}'])
        self.assertEqual(sp.feed(b'2}\n'), [b'{"b":2}'])
        self.assertEqual(sp.tail(), b"")

    def test_multibyte_char_split_across_chunks(self):
        line = "蓝莓🍇".encode("utf-8") + b"\n"
        sp = ByteLineSplitter()
        out = []
        for i in range(len(line)):
            out.extend(sp.feed(line[i:i + 1]))
        self.assertEqual(out, [line[:-1]])
        self.assertEqual(out[0].decode("utf-8"), "蓝莓🍇")

    def test_partial_tail_waits_for_completion(self):
        sp = ByteLineSplitter()
        self.assertEqual(sp.feed(b"abc"), [])
        self.assertEqual(sp.tail(), b"abc")
        self.assertEqual(sp.feed(b"def\n"), [b"abcdef"])

    def test_empty_lines_are_emitted(self):
        sp = ByteLineSplitter()
        self.assertEqual(sp.feed(b"\n\nx\n"), [b"", b"", b"x"])


if __name__ == "__main__":
    unittest.main()
