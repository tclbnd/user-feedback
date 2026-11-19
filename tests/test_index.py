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

    def test_delete_after_flush(self):
        ix = InvertedIndex()
        ix.add("a", "hello world")
        ix.flush()
        ix.delete("a")
        self.assertEqual(ix.search("hello"), [])
        self.assertEqual(ix.search("world"), [])

    def test_update_after_flush_hides_old_text(self):
        ix = InvertedIndex()
        ix.add("a", "old title keywords")
        ix.flush()
        ix.update("a", "new title")
        self.assertEqual(ix.search("old"), [])
        self.assertEqual(ix.search("keywords"), [])
        self.assertEqual(ix.search("new"), ["a"])

    def test_delete_then_readd(self):
        ix = InvertedIndex()
        ix.add("a", "first text")
        ix.flush()
        ix.delete("a")
        ix.add("a", "second text")
        self.assertEqual(ix.search("first"), [])
        self.assertEqual(ix.search("second"), ["a"])

    def test_phrase_requires_order_and_adjacency(self):
        ix = InvertedIndex()
        ix.add("a", "the quick brown fox jumps")
        ix.add("b", "brown quick red fox")
        ix.add("c", "quick red red brown")
        self.assertEqual(ix.phrase("quick brown"), ["a"])
        self.assertEqual(ix.phrase("brown quick"), ["b"])
        self.assertEqual(ix.phrase("quick fox"), [])
        self.assertEqual(ix.search("quick fox"), ["a", "b"])

    def test_phrase_reflects_latest_version(self):
        ix = InvertedIndex()
        ix.add("a", "quick brown fox")
        ix.flush()
        ix.update("a", "brown quick fox")
        self.assertEqual(ix.phrase("quick brown"), [])
        self.assertEqual(ix.phrase("brown quick"), ["a"])

    def test_snippet_marks_words(self):
        self.assertIn("[fox]", snippet("the quick brown fox jumps", "fox"))


if __name__ == "__main__":
    unittest.main()
