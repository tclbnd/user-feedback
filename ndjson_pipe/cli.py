"""Command line entry point: ``python -m ndjson_pipe ...``"""

import argparse
import json
import sys

from .importer import CrashPlan, import_source
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ndjson_pipe",
        description="Resumable streaming NDJSON collection and ingestion pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="ingest (or resume) one NDJSON source")
    p_import.add_argument("--db", required=True, help="SQLite database path")
    p_import.add_argument("--source-id", required=True, help="logical source identifier")
    p_import.add_argument("--file", required=True, help="NDJSON file to read")
    p_import.add_argument(
        "--sealed",
        choices=("true", "false"),
        default="true",
        help="whether the source is sealed (false keeps a trailing partial line)",
    )
    p_import.add_argument("--chunk-size", type=int, default=64 * 1024)
    p_import.add_argument("--batch-size", type=int, default=200)
    p_import.add_argument(
        "--full-scan",
        action="store_true",
        help="scan from byte 0 even if a checkpoint exists (idempotent replay / conflict detection)",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "import":
        with Store(args.db) as store:
            stats = import_source(
                store,
                args.source_id,
                args.file,
                sealed=args.sealed == "true",
                chunk_size=args.chunk_size,
                batch_size=args.batch_size,
                full_scan=args.full_scan,
                crash_plan=CrashPlan.from_env(),
            )
            json.dump(stats.as_dict(), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 0
    return 2  # pragma: no cover - argparse rejects earlier


if __name__ == "__main__":
    raise SystemExit(main())
