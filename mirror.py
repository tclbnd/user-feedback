"""Local graph replay baseline."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("graph",type=Path)
    p.add_argument("state",type=Path)
    p.add_argument("--workers",type=int,default=1)
    p.add_argument("--limit",type=int)
    a=p.parse_args()
    g=json.loads(a.graph.read_text())
    queue=list(g["seeds"])
    visited=set()
    resources=[]
    while queue and (a.limit is None or len(visited)<a.limit):
        url=queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        page=g["pages"].get(url,{})
        if "body" in page:
            resources.append(url)
            queue+=page.get("links",[])
    result={"resources":sorted(resources),"aliases":{},"edges":[],"skipped":[],"requests":[],"done":not queue}
    a.state.write_text(json.dumps(result)+"\n")
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
