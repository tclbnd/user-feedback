"""Simple WebVTT cutter used as the starting point for the EDL feature."""
import argparse
import json
import re
from pathlib import Path


TIMING=re.compile(r"(\d+:\d{2}:\d{2}\.\d{3}) --> (\d+:\d{2}:\d{2}\.\d{3})(.*)")


def millis(value):
    h,m,s=re.split(":",value)
    sec,ms=s.split(".")
    return ((int(h)*60+int(m))*60+int(sec))*1000+int(ms)


def timestamp(value):
    sec,ms=divmod(value,1000)
    hours,sec=divmod(sec,3600)
    minute,second=divmod(sec,60)
    return f"{hours:02}:{minute:02}:{second:02}.{ms:03}"


def cut(edl_path):
    doc=json.loads(edl_path.read_text())
    parts=["WEBVTT"]
    sources=[]
    for clip in doc["clips"][:1]:
        path=edl_path.parent/doc["sources"][clip["source"]]
        lo=clip["start_frame"]*1001//30
        hi=clip["end_frame"]*1001//30
        for block in path.read_text().strip().split("\n\n"):
            rows=block.splitlines()
            for i,row in enumerate(rows):
                m=TIMING.fullmatch(row)
                if m is not None:
                    start=max(millis(m[1]),lo)
                    end=min(millis(m[2]),hi)
                    if start<end:
                        parts.append("\n".join([*rows[:i],f"{timestamp(start-lo)} --> {timestamp(end-lo)}{m[3]}",*rows[i+1:]]))
                    break
    return "\n\n".join(parts)+"\n\n",{"cues":sources}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("edl",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("map",type=Path)
    args=parser.parse_args()
    text,source_map=cut(args.edl)
    args.output.write_text(text)
    args.map.write_text(json.dumps(source_map)+"\n")


if __name__=="__main__":
    main()
