"""Columnar import prototype that treats the complete source event as one row."""
import argparse
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("ingest")
    a.add_argument("source",type=Path)
    a.add_argument("--crash-before-publish",action="store_true")
    q=sub.add_parser("inspect")
    q.add_argument("--version",type=int)
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    if a.op=="ingest":
        rows=[json.loads(line) for line in a.source.read_text().splitlines()]
        pq.write_table(pa.Table.from_pylist(rows),a.root/"events.parquet")
        out={"version":1}
    else:
        out={"version":int((a.root/"events.parquet").exists())}
    print(json.dumps(out,sort_keys=True))


if __name__=="__main__":
    main()
