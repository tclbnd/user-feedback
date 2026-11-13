"""Run every acceptance suite in order and report a single status line."""
import sys
import time

import test_correctness
import test_reuse
import test_scaling
import test_semantics


def main():
    suites = [
        ("correctness (1000 ranges x 40 edits vs independent oracle)",
         test_correctness.main),
        ("scaling + fault injection", test_scaling.main),
        ("EDL reuse invariants", test_reuse.main),
        ("semantic edge cases", test_semantics.main),
    ]
    started = time.time()
    for name, fn in suites:
        print("== %s" % name, flush=True)
        fn()
    print("ALL SUITES PASSED in %.1fs" % (time.time() - started))


if __name__ == "__main__":
    sys.exit(main())
