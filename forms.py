"""A prototype HTML form extractor."""
import argparse
import json
from pathlib import Path
import re


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    q=sub.add_parser("collect")
    q.add_argument("jobs",type=Path)
    q.add_argument("--crash",choices=["during-extract","after-save"])
    sub.add_parser("inspect")
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    dest=a.root/"view.json"
    state=json.loads(dest.read_text()) if dest.exists() else {"rows":[],"review":[]}
    if a.op=="collect":
        for job in json.loads(a.jobs.read_text())["jobs"]:
            html=(a.jobs.parent/job["snapshots"][0]).read_text()
            values=re.findall(r'<input [^>]*value="([^"]+)"',html)
            state["rows"].append({"id":job["id"],"values":values})
        dest.write_text(json.dumps(state)+"\n")
    print(json.dumps(state,sort_keys=True))


if __name__=="__main__":
    main()
