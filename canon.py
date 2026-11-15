import json,sys,hashlib
req=json.load(open(sys.argv[1]))
pages={p["url"]:p for p in req["pages"]}
# 缺陷基线:沿 canonical 链走,环处按输入序停在先见者;hreflang 单向声明照单全收
order=[p["url"] for p in req["pages"]]
seen={}
def root(u):
    path=[]
    while u in pages and pages[u].get("canonical") and pages[u]["canonical"]!=u and pages[u]["canonical"] not in path:
        path.append(u);u=pages[u]["canonical"]
    return u
for u in order:
    r=root(u);seen.setdefault(r,[]).append(u)
clusters=[{"rep":r,"members":sorted(m)} for r,m in sorted(seen.items())]
verdicts={u:("canonical->"+root(u) if root(u)!=u else "self") for u in order}
res={"clusters":clusters,"verdicts":verdicts,
     "digest":hashlib.sha1(str(clusters).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
