import json,sys,hashlib
req=json.load(open(sys.argv[1]))
x=list(req["samples"])
# 缺陷基线:不检测爆点,只对零段做线性插值
out=list(x);rep=[]
i=0
while i<len(out):
    if out[i]==0.0:
        j=i
        while j<len(out) and out[j]==0.0:j+=1
        a=out[i-1] if i>0 else 0.0;b=out[j] if j<len(out) else 0.0
        for k in range(i,j):out[k]=a+(b-a)*(k-i+1)/(j-i+1)
        rep.append([i,j-i]);i=j
    else:i+=1
res={"out":out,"repairs":rep,
     "digest":hashlib.sha1(",".join(f"{v:.4f}" for v in out[:64]).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
