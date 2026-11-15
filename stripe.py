import json,sys,hashlib
req=json.load(open(sys.argv[1]))
k,m=req["k"],req["m"]
blocks=[b.encode() for b in req["blocks"]]
size=max(len(b) for b in blocks)
blocks=[b.ljust(size,b"\0") for b in blocks]
# 缺陷基线:只做单 XOR 校验,忽略 m>1;双块失联返回损坏数据;重建破坏校验
par=[bytes(x^y for x,y in zip(*[blocks])) if False else b""]
p0=bytearray(size)
for b in blocks:
    for i in range(size): p0[i]^=b[i]
parity=[bytes(p0)]
dead=set();reads=[];stats={"degraded_reads":0,"rebuilds":0};errors=[]
cur=list(blocks)+list(parity)
for o in req["ops"]:
    if o["op"]=="fail":
        ds=o["disk"] if isinstance(o["disk"],list) else [o["disk"]]
        dead.update(ds)
    elif o["op"]=="read":
        lost=[i for i in dead if i<k]
        if not lost:
            reads.append({"ok":True,"data":b"".join(cur[:k]).decode(errors="replace"),"degraded":False})
        elif len(lost)==1:
            stats["degraded_reads"]+=1
            r=bytearray(parity[0])
            for i in range(k):
                if i not in dead:
                    for j in range(size): r[j]^=cur[i][j]
            cur[lost[0]]=bytes(r)
            reads.append({"ok":True,"data":b"".join(cur[:k]).decode(errors="replace"),"degraded":True})
        else:
            # 缺陷:双块失联不报错,直接返回含空洞数据
            d=bytearray(parity[0])
            for i in range(k):
                if i not in dead:
                    for j in range(size): d[j]^=cur[i][j]
            for i in dead:
                if i<k: cur[i]=bytes(size)
            reads.append({"ok":True,"data":b"".join(cur[:k]).decode(errors="replace"),"degraded":True})
    elif o["op"]=="rebuild":
        stats["rebuilds"]+=1
        # 缺陷:重建只写回数据块不更新校验
        for i in list(dead):
            if i<k: cur[i]=bytes(size)
        dead.clear()
res={"reads":reads,"errors":errors,"stats":stats,
     "digest":hashlib.sha1(json.dumps([r.get("data") for r in reads],sort_keys=True).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
