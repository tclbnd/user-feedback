"""Small legacy Parquet directory importer."""
import argparse
import json
from pathlib import Path
import pyarrow.parquet as pq


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    rows=[]
    for file in sorted(a.input.glob("*.parquet")):
        rows+=pq.read_table(file).to_pylist()
    a.output.write_text(json.dumps({"rows":rows,"rejects":[]},default=str,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
