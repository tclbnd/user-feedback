"""Command-line entry points.

  python3 -m pipeline.cli ingest DB SOURCE_ID PATH [--chunk-size N] [--seal]
  python3 -m pipeline.cli dump DB SOURCE_ID
  python3 -m pipeline.cli verify DB SOURCE_ID PATH
  python3 -m pipeline.cli reset-checkpoint DB SOURCE_ID OFFSET
"""

import argparse
import sys

from . import importer
from .reference import canonical_rows, reference_rows
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pipeline.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("db")
    p.add_argument("source_id")
    p.add_argument("path")
    p.add_argument("--chunk-size", type=int, default=65536)
    p.add_argument("--seal", action="store_true")

    p = sub.add_parser("dump")
    p.add_argument("db")
    p.add_argument("source_id")

    p = sub.add_parser("verify")
    p.add_argument("db")
    p.add_argument("source_id")
    p.add_argument("path")

    p = sub.add_parser("reset-checkpoint")
    p.add_argument("db")
    p.add_argument("source_id")
    p.add_argument("offset", type=int)

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        store = Store(args.db)
        try:
            stats = importer.ingest(store, args.source_id, args.path,
                                    chunk_size=args.chunk_size,
                                    seal=args.seal)
        finally:
            store.close()
        print("ingest %s: %s" % (args.source_id, stats.as_dict()))
        return 0

    if args.cmd == "dump":
        store = Store(args.db)
        try:
            sys.stdout.write(store.dump(args.source_id))
        finally:
            store.close()
        return 0

    if args.cmd == "verify":
        store = Store(args.db)
        try:
            got = canonical_rows(store, args.source_id)
        finally:
            store.close()
        with open(args.path, "rb") as fh:
            want = reference_rows(fh.read())
        if got != want:
            print("verify FAILED: stored rows differ from batch reference",
                  file=sys.stderr)
            return 1
        print("verify OK: %d rows match the batch reference" % len(got))
        return 0

    if args.cmd == "reset-checkpoint":
        store = Store(args.db)
        try:
            store.reset_checkpoint(args.source_id, args.offset)
        finally:
            store.close()
        print("checkpoint for %s reset to %d" % (args.source_id, args.offset))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
