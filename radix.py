import json,sys,hashlib
req=json.load(open(sys.argv[1]))
pairs=[(k,v) for k,v in req["pairs"]]
# 缺陷基线:Python sorted 按 key 排但相等键次序不保证按原始序(直接按 (k,v) 元组排);
# 且各遍之间不稳定 + 线程/分块参数影响排列
pairs.sort(key=lambda p:(p[0],-p[1]))  # 等键按下标逆序——破坏稳定性
res={"sorted":[[k,v] for k,v in pairs],
     "pass_stats":[],
     "stable_check":False,
     "digest":hashlib.sha1(str(pairs).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
