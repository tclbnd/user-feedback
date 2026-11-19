import unittest

from ftsearch.index import InvertedIndex
from ftsearch.merge import BackgroundMerge, merge_segments, merge_tier, pick_tier


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

    def test_merge_keeps_only_latest_version(self):
        ix = InvertedIndex()
        ix.add("a", "first version")
        ix.flush()
        ix.update("a", "second version")
        ix.flush()
        self.assertTrue(merge_segments(ix))
        self.assertEqual(ix.search("first"), [])
        self.assertEqual(ix.search("second"), ["a"])
        self.assertEqual(ix.stats()["segments"], 1)

    def test_merge_purges_deleted_postings(self):
        ix = InvertedIndex()
        ix.add("a", "gone article")
        ix.flush()
        ix.delete("a")
        ix.add("b", "live article")
        ix.flush()
        self.assertTrue(merge_segments(ix))
        self.assertEqual(ix.search("gone"), [])
        self.assertEqual(ix.search("article"), ["b"])
        # the merged segment carries no postings or tombstone for "a"
        self.assertNotIn("a", ix.segments[0].docs)
        self.assertNotIn("a", ix.versions)

    def test_background_merge_with_concurrent_delete(self):
        ix = InvertedIndex()
        ix.add("a", "alpha")
        ix.flush()
        ix.add("b", "beta")
        ix.flush()
        job = BackgroundMerge(ix, pick_tier(ix)).begin()
        ix.delete("a")  # editor deletes while the merge is running
        self.assertEqual(ix.search("alpha"), [])
        job.commit()
        self.assertEqual(ix.search("alpha"), [])
        self.assertEqual(ix.search("beta"), ["b"])
        self.assertNotIn("a", ix.segments[0].docs)
        self.assertNotIn("a", ix.versions)

    def test_background_merge_with_concurrent_update(self):
        ix = InvertedIndex()
        ix.add("a", "old text")
        ix.flush()
        ix.add("b", "other")
        ix.flush()
        job = BackgroundMerge(ix, pick_tier(ix)).begin()
        ix.update("a", "new text")
        ix.flush()
        job.commit()
        self.assertEqual(ix.search("old"), [])
        self.assertEqual(ix.search("new"), ["a"])


if __name__ == "__main__":
    unittest.main()
