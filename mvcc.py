import json,sys,hashlib
req=json.load(open(sys.argv[1]))
data={};vers={};txs={};seq=0;reads=[];reclaimed=0
for o in req["ops"]:
    t=o["op"];tx=o.get("tx")
    if t=="begin":seq+=1;txs[tx]={"begin":seq,"writes":{},"active":True}
    elif t=="write":txs[tx]["writes"][o["k"]]=o["v"]
    elif t=="commit":
        seq+=1
        for k,v in txs[tx]["writes"].items():
            vers.setdefault(k,[]).append((seq,v));data[k]=v
        txs[tx]["active"]=False
    elif t=="read":
        k=o["k"]
        # 缺陷:总读最新已提交,忽略快照ts与读己之写顺序
        v=txs[tx]["writes"].get(k,data.get(k))
        reads.append({"tx":tx,"k":k,"v":v})
    elif t=="gc":
        # 缺陷:按最新commit回收,无视活跃快照
        for k in vers:
            while len(vers[k])>1:
                vers[k].pop(0);reclaimed+=1
    elif t=="end":txs[tx]["active"]=False
res={"reads":reads,"versions":{k:v for k,v in vers.items()},
     "gc_stats":{"reclaimed":reclaimed},
     "digest":hashlib.sha1(str(reads).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
