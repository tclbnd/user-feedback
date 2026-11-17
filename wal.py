"""Archived log segments `wal/<first>-<last>.jsonl` read as one stream."""
import json
import os


def read(wal_dir):
    """Return the archived records keyed by lsn."""
    records = {}
    for name in sorted(n for n in os.listdir(wal_dir) if n.endswith(".jsonl")):
        with open(os.path.join(wal_dir, name), "rb") as fh:
            for line in fh.read().split(b"\n"):
                if line.strip():
                    rec = json.loads(line)
                    records[rec["lsn"]] = rec
    return records
