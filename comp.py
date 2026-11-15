import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
x=req["samples"];th=10**(req["thresh_db"]/20);ratio=req["ratio"]
out=[];gain=[]
# 缺陷基线:无预读(过冲),硬拐点(kink),增益无速率限制(泵浦)
for v in x:
    lvl=abs(v)
    g=1.0
    if lvl>th:g=(th*(lvl/th)**(1/ratio))/lvl if lvl>0 else 1.0
    gain.append(g);out.append(v*g)
res={"out":out,"gain":gain,
     "stats":{"triggers":sum(1 for g in gain if g<1)},
     "digest":hashlib.sha1(",".join(f"{v:.4f}" for v in out[:64]).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
