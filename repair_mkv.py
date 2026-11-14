"""Container repair command-line starter."""
import argparse
import json
from pathlib import Path


def size_vint(data,offset):
    n=next((i for i in range(1,9) if data[offset] & (1<<(8-i))),None)
    if n is None or offset+n>len(data):
        raise ValueError(f"invalid size at {offset}")
    x=int.from_bytes(data[offset:offset+n],"big") & ((1<<(7*n))-1)
    return n,None if x==(1<<(7*n))-1 else x


def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("report",type=Path)
    a=p.parse_args()
    a.output.write_bytes(a.source.read_bytes())
    a.report.write_text(json.dumps({"recovered":[],"discarded":[]})+"\n")


if __name__=="__main__":
    main()
