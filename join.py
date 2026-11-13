"""External join front end and an incomplete single-partition fallback."""
import argparse
import csv
import json
from pathlib import Path
import resource
import sys


def read_rows(path):
    with path.open(newline="",encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def join(left,right,out,mode,memory_mib,crash):
    out.mkdir()
    lookup={(x["key_a"],x["key_b"]):x["value"] for x in read_rows(right)}
    n=0
    with (out/"rows.csv").open("w",newline="",encoding="utf-8") as target:
        writer=csv.writer(target)
        writer.writerow(["key_a","key_b","left_value","right_value"])
        for row in read_rows(left):
            key=row["key_a"],row["key_b"]
            if key in lookup or mode=="left":
                writer.writerow([*key,row["value"],lookup.get(key)])
                n+=1
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    metrics={"rows":n,"spill_bytes":0,"peak_rss_bytes":rss if sys.platform=="darwin" else rss*1024}
    (out/"_SUCCESS").write_text(json.dumps(metrics)+"\n")
    return metrics


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("left",type=Path)
    parser.add_argument("right",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--type",choices=["inner","left"],default="inner")
    parser.add_argument("--memory-mib",type=int,default=64)
    parser.add_argument("--crash-before-publish",action="store_true")
    args=parser.parse_args()
    print(json.dumps(join(args.left,args.right,args.output,args.type,args.memory_mib,
                          args.crash_before_publish),sort_keys=True))


if __name__=="__main__":
    main()
