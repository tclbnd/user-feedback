import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
gx,gy,gz=req["grid"];B=req["bounds"]
mn,mx=B[0],B[1]
clusters=[[[] for _ in range(gz)]for _ in range(gy)]
clusters=[[[ ] for _ in range(gx*gy*gz)]]
cell=[[] for _ in range(gx*gy*gz)]
def cid(i,j,k):return (k*gy+j)*gx+i
for li,L in enumerate(req["lights"]):
    p=L["pos"]
    i=int((p[0]-mn[0])/(mx[0]-mn[0])*gx);j=int((p[1]-mn[1])/(mx[1]-mn[1])*gy);k=int((p[2]-mn[2])/(mx[2]-mn[2])*gz)
    i=max(0,min(gx-1,i));j=max(0,min(gy-1,j));k=max(0,min(gz-1,k))
    # 缺陷:只放进包含球心的单格(球跨格漏光),spot 不收缩
    cell[cid(i,j,k)].append(li)
res={"clusters":cell,"counts":[len(c) for c in cell],
     "digest":hashlib.sha1(str(cell).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
