import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
a,b=req["a"],req["b"];L=req["len"]
# 缺陷基线:线性等幅淡化(中点-3dB 下凹),无相位对齐,剪接点硬切
out=[]
for i in range(L):
    t=i/(L-1)
    out.append(a[i]*(1-t)+b[i]*t)
res={"out":out,"splice_pos":L//2,
     "stats":{"mid_energy":sum(v*v for v in out[L//2-8:L//2+8])},
     "digest":hashlib.sha1(",".join(f"{v:.4f}" for v in out[:64]).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
