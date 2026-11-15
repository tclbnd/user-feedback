import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
tris=req["tris"]
def centroid(t):return [(t[0][i]+t[1][i]+t[2][i])/3 for i in range(3)]
def bbox(ts):
    mn=[min(v[i] for t in ts for v in t) for i in range(3)]
    mx=[max(v[i] for t in ts for v in t) for i in range(3)]
    return mn,mx
# 缺陷基线:中位切分(非SAH),遍历取首个命中而非最近
def build(ts):
    if len(ts)<=req.get("leaf",4):return {"leaf":ts,"box":bbox(ts)}
    c=[centroid(t) for t in ts]
    ax=max(range(3),key=lambda i:max(v[i] for v in c)-min(v[i] for v in c))
    ts2=sorted(ts,key=lambda t:centroid(t)[ax])
    mid=len(ts2)//2
    return {"l":build(ts2[:mid]),"r":build(ts2[mid:]),"box":bbox(ts)}
def hit(b,o,d,tn,tf):
    if "leaf" in b:
        best=None
        for t in b["leaf"]:
            h=moller(o,d,t)
            if h is not None and tn<h<tf:best=h;tf=h
        return best
    for c in (b["l"],b["r"]):
        if slab(o,d,c["box"],tn,tf):
            h=hit(c,o,d,tn,tf)
            if h is not None:return h  # 缺陷:左子树命中即返,不比右子树
    return None
def slab(o,d,box,tn,tf):
    for i in range(3):
        inv=1/(d[i] or 1e-30)
        t0=(box[0][i]-o[i])*inv;t1=(box[1][i]-o[i])*inv
        if inv<0:t0,t1=t1,t0
        tn=max(tn,t0);tf=min(tf,t1)
        if tn>tf:return False
    return True
def moller(o,d,t):
    e1=[t[1][i]-t[0][i] for i in range(3)];e2=[t[2][i]-t[0][i] for i in range(3)]
    p=[d[1]*e2[2]-d[2]*e2[1],d[2]*e2[0]-d[0]*e2[2],d[0]*e2[1]-d[1]*e2[0]]
    det=sum(e1[i]*p[i] for i in range(3))
    if abs(det)<1e-12:return None
    inv=1/det;sv=[o[i]-t[0][i] for i in range(3)]
    u=sum(sv[i]*p[i] for i in range(3))*inv
    if u<0 or u>1:return None
    q=[sv[1]*e1[2]-sv[2]*e1[1],sv[2]*e1[0]-sv[0]*e1[2],sv[0]*e1[1]-sv[1]*e1[0]]
    v=sum(d[i]*q[i] for i in range(3))*inv
    if v<0 or u+v>1:return None
    return sum(e2[i]*q[i] for i in range(3))*inv
tree=build(tris)
hits=[]
for r in req["rays"]:
    t=hit(tree,r["o"],r["d"],1e-9,1e30)
    hits.append({"t":t if t else -1.0,"tri":-1})
def height(b):return 1 if "leaf" in b else 1+max(height(b["l"]),height(b["r"]))
def nodes(b):return 1 if "leaf" in b else 1+nodes(b["l"])+nodes(b["r"])
res={"hits":hits,"height":height(tree),"nodes":nodes(tree),"sah_cost":0.0,
     "digest":hashlib.sha1(str(hits).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
