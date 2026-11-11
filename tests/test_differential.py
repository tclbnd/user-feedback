import os
import random
import unittest

from pipeline import corpus

ARTIFACTS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "artifacts")
COVERAGE_PATH = os.path.join(ARTIFACTS, "coverage.json")


class DifferentialTest(unittest.TestCase):
    """Generated valid corpora (with fixed reject samples interleaved) are
    ingested under random chunk sizes, append orders and replay counts, then
    compared against the independent batch reference.  Coverage feedback
    steers generation toward the least-exercised combinations."""

    CASES = 60

    def test_corpus_vs_reference(self):
        coverage = corpus.load_coverage(COVERAGE_PATH)
        rng = random.Random(20261112)
        try:
            for _ in range(self.CASES):
                combo = corpus.pick_combo(rng, coverage)
                corpus.record_coverage(coverage, combo)
                with self.subTest(combo=combo):
                    corpus.run_case(combo, rng)
        finally:
            corpus.save_coverage(COVERAGE_PATH, coverage)


if __name__ == "__main__":
    unittest.main()
