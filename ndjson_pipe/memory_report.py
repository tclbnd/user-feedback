"""CLI wrapper for the bounded-memory acceptance measurement."""

import json
import sys

from ndjson_pipe.memory_acceptance import memory_acceptance


def main() -> int:
    workdir = sys.argv[1] if len(sys.argv) > 1 else "."
    report = memory_acceptance(workdir)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if not report["bounded"]:
        print("FAIL: peak memory scaled with source size", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
