# 表面积启发式 BVH 构建与遍历命中一致性

`python3 bvh.py input.json output.json`

input.json: `{"tris":[[[ax,ay,az],[bx,by,bz],[cx,cy,cz]]...],"rays":[{"o":[..],"d":[..]}...],"leaf":4,"seed":1}`

模型约定：
- SAH 代价模型分桶选最优划分平面;质心全重合退化时仍能建树
- 遍历返回全树最近命中(不是先碰到的);命中距离与暴力参考逐射线一致
- 输出 `{"hits":[{"t":f,"tri":i}...],"height":n,"nodes":n,"sah_cost":f,"digest":"sha1"}`
- 三角形输入顺序变化不改变命中结果

缺陷基线:中位切分+左子树命中即返(非最近)。
