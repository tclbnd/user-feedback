"""Bounded-memory tests: peak allocation must not scale with source size."""

import tempfile
import unittest

from ndjson_pipe.memory_acceptance import memory_acceptance


class MemoryTests(unittest.TestCase):
    def test_peak_memory_is_bounded(self):
        workdir = tempfile.mkdtemp(prefix="ndjson-mem-test-")
        report = memory_acceptance(
            workdir,
            small_records=1_000,
            large_records=10_000,
            chunk_size=128,
            batch_size=25,
        )
        # the big source is hundreds of times larger than one read buffer
        self.assertGreater(report["large"]["bytes"], report["chunk_size"] * 500)
        self.assertTrue(
            report["bounded"],
            msg=f"peak grew with file size: {report}",
        )
        # absolute sanity: streaming a multi-MB source stays well under 5 MB
        # of traced Python allocation regardless of file size
        self.assertLess(report["large"]["peak_python_bytes"], 5 * 1024 * 1024)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
