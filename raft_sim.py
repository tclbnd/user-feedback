#!/usr/bin/env python3
"""Deterministic raft-lite replication simulator (buggy baseline)."""
import json, sys

def simulate(sc):
    n = sc["nodes"]
    nodes = [{"id": i, "term": 0, "role": "follower", "voted_for": None,
              "log": [], "commit_idx": -1, "applied": []} for i in range(n)]
    groups = [list(range(n))]
    rejected, term_log = [], []

    def peers(i):
        for g in groups:
            if i in g:
                return g
        return [i]

    def elect(i, t):
        nd = nodes[i]
        nd["term"] += 1                      # BUG: no majority check, no vote tracking
        nd["role"] = "leader"
        nd["voted_for"] = i
        term_log.append({"node": i, "term": nd["term"], "elected_at": t})

    for e in sorted(sc["events"], key=lambda x: x["t"]):
        k = e["kind"]
        if k == "partition":
            groups = e["groups"]
        elif k == "heal":
            groups = [list(range(n))]
        elif k == "client_write":
            i, v = e["to"], e["value"]
            nd = nodes[i]
            if nd["role"] != "leader":
                elect(i, e["t"])
                if nd["role"] != "leader":
                    rejected.append({"t": e["t"], "to": i, "value": v, "reason": "no-majority"})
                    continue
            nd["log"].append({"term": nd["term"], "value": v})
            idx = len(nd["log"]) - 1
            for j in peers(i):
                if j == i:
                    continue
                f = nodes[j]
                f["log"].append({"term": nd["term"], "value": v})   # BUG: no conflict truncation
                f["term"] = max(f["term"], nd["term"])
            nd["commit_idx"] = len(nd["log"]) - 1                   # BUG: unconditional commit
            for j in peers(i):
                nodes[j]["commit_idx"] = nodes[j]["commit_idx"] if nodes[j]==nd else min(idx, len(nodes[j]["log"])-1)
            for ndd in nodes:
                ndd["applied"] = [x["value"] for x in ndd["log"][:ndd["commit_idx"]+1]]
    return {"nodes": [{k: nd[k] for k in ("id","term","role","log","commit_idx","applied")} for nd in nodes],
            "rejected": rejected, "term_log": term_log}

def main():
    sc = json.load(open(sys.argv[1]))
    json.dump(simulate(sc), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
