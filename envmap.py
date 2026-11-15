import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
w,h=req["w"],req["h"];sc=req["scene"]
def L(theta,phi):
    # 解析环境场景(契约固定):
    if sc["type"]=="uniform": return sc.get("v",1.0)
    if sc["type"]=="dirlight":
        dt,dp=sc["dir"];x,y,z=math.sin(theta)*math.cos(phi),math.cos(theta),math.sin(theta)*math.sin(phi)
        dx,dy,dz=math.sin(dt)*math.cos(dp),math.cos(dt),math.sin(dt)*math.sin(dp)
        dot=x*dx+y*dy+z*dz
        return sc.get("i",5.0) if dot>math.cos(0.05) else 0.0
    if sc["type"]=="gradient": return sc.get("lo",0.1)+(sc.get("hi",1.5)-sc.get("lo",0.1))*(0.5+0.5*math.cos(theta))
    if sc["type"]=="patches":
        for t0,t1,p0,p1,v in sc["rects"]:
            if t0<=theta<t1 and p0<=phi<p1: return v
        return sc.get("bg",0.02)
    return 0.0
# 缺陷基线:等纹素加权(无 sin(theta) 立体角),irradiance 无 A_l 卷积缩放
Y=[lambda x,y,z:1.0,
   lambda x,y,z:y,lambda x,y,z:z,lambda x,y,z:x,
   lambda x,y,z:x*y,lambda x,y,z:y*z,lambda x,y,z:3*z*z-1,lambda x,y,z:x*z,lambda x,y,z:x*x-y*y]
C=[0.282095,0.488603,0.488603,0.488603,1.092548,1.092548,0.315392,1.092548,0.546274]
co=[0.0]*9
for i in range(h):
    th=math.pi*(i+0.5)/h
    for j in range(w):
        ph=2*math.pi*(j+0.5)/w
        x,y,z=math.sin(th)*math.cos(ph),math.cos(th),math.sin(th)*math.sin(ph)
        v=L(th,ph)
        for m in range(1,9): co[m]+=v*C[m]*Y[m](x,y,z)  # 缺陷:无 dOmega
        co[0]+=v*C[0]
irr=[]
for th,ph in req.get("dirs",[]):
    x,y,z=math.sin(th)*math.cos(ph),math.cos(th),math.sin(th)*math.sin(ph)
    irr.append(sum(co[m]*C[m]*Y[m](x,y,z) for m in range(9)))  # 缺陷:无卷积核缩放
res={"coeffs":co,"irradiance":irr,"block_count":req.get("blocks",1),
     "digest":hashlib.sha1(",".join(f"{c:.4f}" for c in co).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
