"""Minimal runner (no pytest dependency); exits non-zero on first failure."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cull  # noqa: E402


def main():
    tests = sorted(
        (name, fn) for name, fn in vars(test_cull).items()
        if name.startswith("test_") and callable(fn)
    )
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL %s" % name)
            traceback.print_exc()
        else:
            print("ok   %s" % name)
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
