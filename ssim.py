import json,sys,math,hashlib
req=json.load(open(sys.argv[1]))
A,B=req["ref"],req["dist"];mode=req.get("window","gauss")
h,w=len(A),len(A[0])
# 缺陷基线:均匀8x8窗(忽略mode),常数错用C1=C2=1,步进4非滑动,无多尺度
K=8;S=4
def stats(i,j):
    xs=[A[y][x] for y in range(i,min(i+K,h)) for x in range(j,min(j+K,w))]
    ys=[B[y][x] for y in range(i,min(i+K,h)) for x in range(j,min(j+K,w))]
    n=len(xs);mx=sum(xs)/n;my=sum(ys)/n
    vx=sum((a-mx)**2 for a in xs)/n;vy=sum((b-my)**2 for b in ys)/n
    cv=sum((xs[t]-mx)*(ys[t]-my) for t in range(n))/n
    return mx,my,vx,vy,cv
sm=[[0.0]*(w//S) for _ in range(h//S)]
for i in range(0,h-K+1,S):
    for j in range(0,w-K+1,S):
        mx,my,vx,vy,cv=stats(i,j)
        sm[i//S][j//S]=(2*mx*my+1)/(mx*mx+my*my+1)*(2*cv+1)/(vx+vy+1)
res={"ssim":sum(sum(r) for r in sm)/sum(len(r) for r in sm),"map":sm,"per_scale":[],
     "digest":hashlib.sha1(str(sm[0][0]).encode()).hexdigest()}
json.dump(res,open(sys.argv[2],"w"))
