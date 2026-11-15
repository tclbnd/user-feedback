import json,sys,hashlib
req=json.load(open(sys.argv[1]));SZ=req["seg_size"]
segs=[[]];ingested=[];checkpoint=0;seg_seq=0
alive=True;log=[]
def cur():return segs[-1]
for o in req["ops"]:
    t=o["op"]
    if t=="append":
        for r in o["records"]:
            if len(cur())>=SZ: segs.append([])
            seg_seq+=1;cur().append({"seq":seg_seq,"batch":o["batch"],"r":r})
    elif t=="checkpoint":
        checkpoint=seg_seq
    elif t=="truncate":
        # 缺陷:按段首位是否低于水位整段删,不管段内尾部记录未过检查点
        segs=[s for s in segs if not(s and s[0]["seq"]<=checkpoint)]
    elif t=="corrupt":
        i=o.get("seg",0);pos=o.get("pos",0)
        if i<len(segs) and pos<len(segs[i]):segs[i][pos]["r"]="<BAD>"
    elif t=="crash":
        pass  # 状态保留在 segs(模拟落盘),内存 ingested 保留已入库
    elif t=="recover":
        # 缺陷:遇损坏记录整段放弃其后的所有记录;且只重放未入库批
        for s in segs:
            ok=True
            for rec in s:
                if rec["r"]=="<BAD>":ok=False;break
                if ok:ingested.append(rec)
    elif t=="ingest":
        # 入库<=检查点的记录
        for s in segs:
            for rec in s:
                if rec["seq"]<=checkpoint:ingested.append(rec)
res={"ingested":sorted({r["batch"] for r in ingested}),
     "segments":[{"first":s[0]["seq"],"last":s[-1]["seq"],"records":len(s)} for s in segs if s],
     "recovered":len(ingested),
     "digest":hashlib.sha1(str(sorted({r["batch"] for r in ingested})).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
