#!/usr/bin/env python3
"""YUV <-> RGB conversion across standards and ranges.

Usage:
    python3 convert.py input.json output.json
    python3 convert.py --selftest
    python3 convert.py --diagnose bt601 bt709
"""

import json
import sys

from yuvkit import run_file
from yuvkit.checks import compare_constants, explain
from yuvkit.converter import ConversionError
from yuvkit.request import RequestError
from yuvkit.ranges import RangeError
from yuvkit.standards import UnknownStandard


def main(argv):
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) > 1 and argv[1] == "--selftest":
        from yuvkit.selftest import main as selftest_main
        return selftest_main(argv[2:])
    if len(argv) > 1 and argv[1] == "--diagnose":
        names = argv[2:] or ["bt601", "bt709"]
        print(json.dumps(compare_constants(names), indent=2))
        return 0
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        res = run_file(argv[1], argv[2])
    except (RequestError, ConversionError, RangeError, UnknownStandard) as exc:
        print("convert.py: invalid request: %s" % exc, file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print("convert.py: cannot read/write: %s" % exc, file=sys.stderr)
        return 4
    for text in explain(res["warnings"]):
        print("warning: %s" % text, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

