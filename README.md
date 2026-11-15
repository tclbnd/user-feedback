# 切线空间重建与镜像UV旋向
`python3 tangent.py mesh.json output.json`
mesh.json: `{"pos":[[x,y,z]],"uv":[[u,v]],"tris":[[a,b,c]]}`
- 逐角 dPos/dUV 累积 T/B,Gram-Schmidt 正交化,handedness=sign((T×B)·N)
- 零面积UV三角形: 切线回退=面法线正交基,记 degenerate
- 输出 `{"tangents":[[x,y,z,w]],"degenerate":n,"digest"}`
缺陷基线: 顶点共享累积不做逐角分裂,镜像岛旋向丢失.
