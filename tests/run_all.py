#!/usr/bin/env python3
"""Run the whole acceptance suite:  python3 tests/run_all.py [-v]

Standard library only (unittest); no pytest / numpy required.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MODULES = [
    "tests.test_curve",
    "tests.test_metering",
    "tests.test_halo",
    "tests.test_invariance",
    "tests.test_robust",
    "tests.test_cli",
]


def main(argv):
    verbosity = 2 if "-v" in argv else 1
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in MODULES:
        suite.addTests(loader.loadTestsFromName(name))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))