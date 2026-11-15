import json,sys,hashlib
req=json.load(open(sys.argv[1]))
x=req["samples"];ceil=10**(req["ceiling_db"]/20)
out=[];gain=[]
# 缺陷基线:逐样本硬钳,无过采样真峰值、无起音/释放包络,块间独立
for v in x:
    g=1.0
    if abs(v)>ceil: g=ceil/abs(v)
    gain.append(g);out.append(v*g)
res={"out":out,"gain":gain,
     "stats":{"max_peak":max(abs(v) for v in out),"limited":sum(1 for g in gain if g<1)},
     "digest":hashlib.sha1(",".join(f"{v:.4f}" for v in out).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
