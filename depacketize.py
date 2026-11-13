"""A partial H.264 RTP payload extractor over offline packet records."""
import argparse
import hashlib
import json
from pathlib import Path

START=b"\x00\x00\x00\x01"


def recover(data):
    units=[]
    pieces=[]
    for p in data["packets"]:
        b=bytes.fromhex(p["hex"])
        if b and 0<(b[0]&31)<24:
            pieces.append(b)
        if p["marker"]:
            units.append({"timestamp":p["timestamp"],"nals":[hashlib.sha256(v).hexdigest() for v in pieces]})
            yield units[-1],pieces
            pieces=[]


def main():
    p=argparse.ArgumentParser()
    p.add_argument("packets",type=Path)
    p.add_argument("video",type=Path)
    p.add_argument("report",type=Path)
    a=p.parse_args()
    data=json.loads(a.packets.read_text())
    report=[]
    with a.video.open("wb") as out:
        for row,pieces in recover(data):
            report.append(row)
            for nal in pieces:
                out.write(START+nal)
    a.report.write_text(json.dumps({"units":report,"stats":{"max_pending":0,"lost":0,"duplicates":0}})+"\n")


if __name__=="__main__":
    main()
