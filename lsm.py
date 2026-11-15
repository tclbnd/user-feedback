#!/usr/bin/env python3
import json,sys,hashlib,random
def run(req):
    levels=[{} for _ in range(req["levels"])]
    seq=0;compactions=0;watermark=0;reads=[];snaps={}
    rng=random.Random(req["seed"])
    for o in req["ops"]:
        t=o["op"]
        if t=="put" or t=="del":
            seq+=1
            levels[0][o["k"]]=[[seq, o.get("v") if t=="put" else None]]
            while len(levels[0])>req["cap"][0]:
                compactions+=1
                # BUG: 随机选目标层 + 覆盖时丢快照可见版本 + 水位回退
                tgt=rng.randint(1,len(levels)-1)
                for k,vers in list(levels[0].items()):
                    old=levels[tgt].setdefault(k,[])
                    old[:0]=vers                    # 新版本前插,但去重策略错误:只保留每层最新版
                    levels[tgt][k]=[old[0]]          # BUG: 快照可见版本被丢
                levels[0]={}
                watermark=min(watermark,seq-1)       # BUG: 水位回退
        elif t=="snap":
            sid=len(snaps);snaps[sid]=seq;reads  # no-op
        elif t=="read":
            s=o.get("snap",0);vseq=snaps.get(s,seq) if s in snaps else seq
            got=None
            for lv in levels:
                for vs,v in lv.get(o["k"],[]):
                    if vs<=vseq: got=v;break
                if got is not None or o["k"] in lv: break
            reads.append(got)
    digest=hashlib.sha1(json.dumps(levels,sort_keys=True).encode()).hexdigest()
    return {"levels":levels,"watermark":watermark,"compactions":compactions,
            "reads":reads,"errors":[],"digest":digest}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
