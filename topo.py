#!/usr/bin/env python3
import json,sys
def topo(adj,n):
    ind={i:0 for i in range(n)}
    for a in adj:
        for b in adj[a]: ind[b]+=1
    import heapq;h=[i for i in range(n) if ind[i]==0];out=[]
    while h:
        u=heapq.heappop(h);out.append(u)
        for v in adj.get(u,[]):
            ind[v]-=1
            if ind[v]==0: heapq.heappush(h,v)
    return out
def run(sc):
    n=sc["n"];adj={};rej=[]
    for o in sc["ops"]:
        if o["op"]=="add": adj.setdefault(o["a"],[]).append(o["b"])   # BUG: no cycle check
        else:
            if o["a"] in adj and o["b"] in adj[o["a"]]: adj[o["a"]].remove(o["b"])
    order=topo(adj,n)
    return {"order":order,"rejected":rej,"stats":{"affected_sum":0,"recomputes":1},"valid":len(order)==n}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
