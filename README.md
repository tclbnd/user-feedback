# 保守体素覆盖

`python3 voxelize.py input.json output.json`。输入 `resolution` 为 2 的幂；工作域为 `[0,resolution]³`，面表 `triangles` 是三角面的数组，每个三角形给出三个 `[x,y,z]` 坐标。体素 `(i,j,k)` 占据闭立方体 `[i,i+1] × [j,j+1] × [k,k+1]`，任意面相交或只碰到顶点、边或面都计入。输出只包含工作域内体素。

输出 `leaves` 是去重、按 `(x,y,z)` 排序的体素三元组。`levels` 按根深度 0 到叶深度 `log2(resolution)` 排列，每层含 `depth`、`count` 和按坐标排序的 `nodes`。节点字段为 `x`、`y`、`z`、`child_mask` 和 `occupied_leaves`；节点坐标为该深度网格的整数坐标，子节点掩码位编号为 `dx + 2*dy + 4*dz`。叶无子节点且只包含一个占用叶。空层输出空列表。`digest` 概括叶列表和层结构，在输入面顺序变化后保持一致。
