import unittest

from ftsearch.index import InvertedIndex
from ftsearch.merge import BackgroundMerge, merge_tier, pick_tier


class MergeTests(unittest.TestCase):
    def test_tier_picks_smallest(self):
        ix = InvertedIndex()
        ix.add("a", "one two three four five six")
        ix.flush()
        ix.add("b", "seven")
        ix.flush()
        ix.add("c", "eight nine")
        ix.flush()
        self.assertEqual([s.seq for s in pick_tier(ix, 2)], [2, 3])
        self.assertTrue(merge_tier(ix))
        self.assertEqual(ix.search("seven"), ["b"])
        self.assertEqual(ix.stats()["segments"], 1)

    def test_background_merge_keeps_new_segments(self):
        ix = InvertedIndex()
        for doc in "abc":
            ix.add(doc, f"word {doc}")
            ix.flush()
        job = BackgroundMerge(ix, pick_tier(ix)).begin()
        ix.add("d", "word d")
        ix.flush()
        job.commit()
        self.assertEqual(ix.search("word"), ["a", "b", "c", "d"])
        self.assertEqual(ix.stats()["merges"], 1)


if __name__ == "__main__":
    unittest.main()
