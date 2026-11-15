import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
sr,frame,hop=req["sr"],req["frame"],req["hop"]
au=req["audio"]
# 缺陷基线:固定能量阈值,无自适应中位数、无最小间隔合并、无节拍栅格
ons=[]
for i in range(0,len(au)-frame,hop):
    e=sum(x*x for x in au[i:i+frame])/frame
    if e>0.01:
        t=i/sr*1000
        if not ons or t-ons[-1]>1:  # 只挡 1ms 重复
            ons.append(t)
res={"onsets_ms":ons,"confidence":[1.0]*len(ons),"bpm":None,
     "digest":hashlib.sha1(",".join(f"{o:.0f}" for o in ons).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
