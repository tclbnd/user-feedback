"""Audit/provenance inspection of a persisted source database."""

import argparse
import json
import sys

from .store import Store


def inspect(db_path: str, source_id: str) -> int:
    with Store(db_path) as store:
        print(f"# source {source_id!r} @ {db_path}")
        print(json.dumps(store.stats(source_id), ensure_ascii=False, indent=2))

        print("\n## accepted records (raw JSON samples retained)")
        rows = store.conn.execute(
            "SELECT line_no, start_offset, record_id, normalized_by, amount, "
            "note_present, note_value, raw_line FROM records "
            "WHERE source_id = ? ORDER BY start_offset",
            (source_id,),
        ).fetchall()
        for row in rows:
            print(json.dumps(
                {"line": row[0], "offset": row[1], "id": row[2],
                 "normalized_by": row[3], "amount": row[4],
                 "note_present": bool(row[5]), "note": row[6],
                 "raw_json": row[7]}, ensure_ascii=False))

        print("\n## rejections (structured reason + raw JSON sample)")
        rows = store.conn.execute(
            "SELECT line_no, start_offset, reason, field, detail, raw_line "
            "FROM rejections WHERE source_id = ? ORDER BY start_offset",
            (source_id,),
        ).fetchall()
        for row in rows:
            print(json.dumps(
                {"line": row[0], "offset": row[1],
                 "reason": {"reason": row[2], "field": row[3], "detail": row[4]},
                 "raw_json": row[5]}, ensure_ascii=False))

        print("\n## conflicts")
        for row in store.dump_conflicts(source_id):
            print(json.dumps(
                {"offset": row[0], "existing_kind": row[1],
                 "existing_hash": row[2], "incoming_hash": row[3]}))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a pipeline database")
    parser.add_argument("--db", required=True)
    parser.add_argument("--source-id", required=True)
    args = parser.parse_args(argv)
    return inspect(args.db, args.source_id)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
