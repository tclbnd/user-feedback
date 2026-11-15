import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
gx,gy,gz=req["grid"];B=req["bounds"]
mn,mx=B[0],B[1]
# 退化边界防护:某轴跨度<=0 时按单位跨度处理(整轴退化为单格段)
span=[mx[a]-mn[a] if mx[a]-mn[a]>0 else 1.0 for a in range(3)]
n=(gx,gy,gz)
cell=[[] for _ in range(gx*gy*gz)]
def cid(i,j,k):return (k*gy+j)*gx+i
def eff_radius(L):
    # 点光: r; 聚光: 锥近似为按半角收缩的球 r_eff = r*sin(half)
    r=float(L.get("r",0.0))
    if L.get("type","point")=="spot":
        half=max(0.0,min(math.pi/2.0,float(L.get("half",0.0))))  # 半角物理上限90°
        return r*math.sin(half)
    return r
for li,L in enumerate(req["lights"]):
    p=[float(c) for c in L["pos"]]
    r=eff_radius(L)
    # 影响球覆盖的格子索引范围(含跨簇边界), clamp 到网格内
    lo=[0,0,0];hi=[0,0,0]
    for a in range(3):
        lo[a]=max(0,min(n[a]-1,int(math.floor((p[a]-r-mn[a])/span[a]*n[a]))))
        hi[a]=max(0,min(n[a]-1,int(math.floor((p[a]+r-mn[a])/span[a]*n[a]))))
    r2=r*r
    eps=1e-9*(1.0+r2)  # 保守 epsilon: 浮点边界处宁可多收不可漏收
    for k in range(lo[2],hi[2]+1):
        z0=mn[2]+span[2]*k/n[2];z1=mn[2]+span[2]*(k+1)/n[2]
        dz=0.0 if z0<=p[2]<=z1 else min(abs(p[2]-z0),abs(p[2]-z1))
        for j in range(lo[1],hi[1]+1):
            y0=mn[1]+span[1]*j/n[1];y1=mn[1]+span[1]*(j+1)/n[1]
            dy=0.0 if y0<=p[1]<=y1 else min(abs(p[1]-y0),abs(p[1]-y1))
            for i in range(lo[0],hi[0]+1):
                x0=mn[0]+span[0]*i/n[0];x1=mn[0]+span[0]*(i+1)/n[0]
                dx=0.0 if x0<=p[0]<=x1 else min(abs(p[0]-x0),abs(p[0]-x1))
                # 球心到簇 AABB 的最近距离平方 <= r^2 即保守相交
                if dx*dx+dy*dy+dz*dz<=r2+eps:
                    cell[cid(i,j,k)].append(li)  # li 递增 => 各簇列表天然按 id 有序
counts=[len(c) for c in cell]
res={"clusters":cell,"counts":counts,
     "stats":{"total_hits":sum(counts),
              "max_per_cluster":max(counts) if counts else 0,
              "empty_clusters":sum(1 for c in counts if c==0)},
     "digest":hashlib.sha1(str(cell).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
