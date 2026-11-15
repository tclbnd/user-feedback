import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
W,H,T=req["w"],req["h"],req["tile"]
prims=req["prims"]
# 缺陷基线:按输入顺序画家算法(不按深度),高斯在瓦片边界截断
px=[[0.0,0.0,0.0] for _ in range(H)for _ in range(W)]
for p in prims:
    cx,cy,sx,sy,sxy,a=p["x"],p["y"],p["sx"],p["sy"],p.get("sxy",0.0),p["a"]
    det=sx*sy-sxy*sxy
    ix,iy,ixy=sy/det,-sxy/det,sx/det
    r=3*math.sqrt(max(sx,sy))
    tx0,ty0=int((cx-r)/T),int((cy-r)/T)
    tx1,ty1=int((cx+r)/T),int((cy+r)/T)
    for ty in range(max(0,ty0),min(H//T+1,ty1+1)):
        for tx in range(max(0,tx0),min(W//T+1,tx1+1)):
            # 缺陷:只在所属瓦片内求值,边缘像素被硬截
            for yy in range(ty*T,min((ty+1)*T,H)):
                for xx in range(tx*T,min((tx+1)*T,W)):
                    dx,dy=xx+0.5-cx,yy+0.5-cy
                    e=math.exp(-0.5*(ix*dx*dx+2*ixy*dx*dy+iy*dy*dy))
                    aa=a*e
                    idx=yy*W+xx
                    for c in range(3):
                        px[idx][c]=px[idx][c]*(1-aa)+p["rgb"][c]*aa
res={"pixels":[px[y*W+x] for y in range(H) for x in range(W)],
     "tile_counts":[[0]*(W//T) for _ in range(H//T)],
     "digest":hashlib.sha1(str(px[0]).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
