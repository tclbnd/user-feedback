import json,sys,math,hashlib,random
req=json.load(open(sys.argv[1]))
samples=req["samples"];bits=req["bits"]
q=1.0/(1<<(bits-1))
out=[]
# 缺陷基线:矩形抖动但无误差反馈整形、不用 seed(不可复现)、忽略 shaping/block
for x in samples:
    d=random.uniform(-q/2,q/2)
    out.append(int((x+d)/q))
res={"out":out,
     "digest":hashlib.sha1(",".join(map(str,out[:64])).encode()).hexdigest(),
     "stats":{"rms":math.sqrt(sum(v*v for v in out)/max(1,len(out)))}}
json.dump(res,open(sys.argv[2],"w"))
