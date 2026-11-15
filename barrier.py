import json,sys,hashlib
req=json.load(open(sys.argv[1]));out=json.load if False else open(sys.argv[2],"w")
S,T,P=req["shards"],req["timeout"],req.get("policy","release")
evs=sorted(req["events"],key=lambda e:e["t"])
batches={};decisions=[]
for b in range(1,req["batches"]+1):
    arr=[e for e in evs if e.get("batch")==b and e["type"]=="arrive"]
    tos=[e for e in evs if e.get("batch")==b and e["type"]=="timeout"]
    lost=[e for e in evs if e.get("batch")==b and e["type"]=="lost"]
    arrived={e["shard"] for e in arr}
    missing=S-len(arrived)
    # 缺陷:不看 timeout 事件;只要缺人就记 pending(挂死),迟到分片混入当前批导致判定分裂
    if missing==0:
        verdict="pass"
    elif lost:
        verdict="pending"  # 失联→永久挂起
    else:
        verdict="pending"
    for s in range(S):
        # 缺陷:各分片按自己是否到达分别下结论 → 分歧
        decisions.append({"shard":s,"batch":b,"verdict":verdict if s in arrived else ("fail" if tos else verdict)})
    batches[b]=verdict
res={"batches":[{"id":b,"result":v} for b,v in batches.items()],
     "decisions":decisions,"digest":hashlib.sha1(json.dumps(batches,sort_keys=True).encode()).hexdigest()}
json.dump(res,out)
