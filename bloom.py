import json,sys,hashlib
req=json.load(open(sys.argv[1]))
B,K=req["buckets"],req["k"];seed=req.get("seed",1)
def h(key,i):
    return int.from_bytes(hashlib.sha1(f"{seed}:{i}:{key}".encode()).digest()[:4],"little")%B
cnt=[0]*B;verdicts=[];n_ins=0
for o in req["ops"]:
    key=o["k"]
    if o["op"]=="ins":
        n_ins+=1
        for i in range(K): cnt[h(key,i)]+=1
    elif o["op"]=="del":
        # 缺陷:盲目递减,其他键共享桶被减 → 假阴性
        for i in range(K): cnt[h(key,i)]=max(0,cnt[h(key,i)]-1)
    elif o["op"]=="query":
        verdicts.append(all(cnt[h(key,i)]>0 for i in range(K)))
# 缺陷:无在线假阳性估计,无饱和预警
res={"verdicts":verdicts,"fp_estimate":0.0,"warn":False,
     "counts":{"inserted":n_ins,"bucket_sum":sum(cnt)},
     "digest":hashlib.sha1(str(sorted(cnt)).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
