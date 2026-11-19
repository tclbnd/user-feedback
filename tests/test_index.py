import unittest

from ftsearch.highlight import snippet
from ftsearch.index import InvertedIndex


class IndexTests(unittest.TestCase):
    def test_word_search_across_segments(self):
        ix = InvertedIndex()
        ix.add("a", "postgres vacuum tuning")
        ix.flush()
        ix.add("b", "mysql tuning tips")
        self.assertEqual(ix.search("tuning"), ["a", "b"])
        self.assertEqual(ix.search("vacuum tuning"), ["a"])

    def test_delete_from_buffer(self):
        ix = InvertedIndex()
        ix.add("a", "hello world")
        ix.delete("a")
        self.assertEqual(ix.search("hello"), [])

    def test_snippet_marks_words(self):
        self.assertIn("[fox]", snippet("the quick brown fox jumps", "fox"))


if __name__ == "__main__":
    unittest.main()
