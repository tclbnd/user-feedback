# 三角网格截面

Python 3.9+。用 `python3 slice_mesh.py demo.json output.json --obj-dir preview`。输入含 mesh 和 plane。mesh.vertices 是三维坐标，mesh.faces 每项含零基 indices 和 material，可带三个逐角 UV。plane 为 normal 和 offset，点积 normal·p=offset 定义切面，normal 可不为单位向量。输入为闭合定向流形且各实体不重叠，可出现乱绕向。几何计算可采用相对 1e-9 量级的容差。

输出 positive 和 negative，各为同样的 mesh 结构。沿切面分割三角形，插值表面已有的 UV，保留原材质。同一网格侧的公共边应共享顶点索引。新封口的 material 使用 `section`，每个封口三角形给出单位 normal 和三个逐角二维 uv；同一截面共享点的 UV 应连续。按闭合实体要求保证封口的外法线和环洞。

平面仅接触顶点、边或共面面片且该实体不跨平面时，将完整实体留在有体积的一侧，另一侧没有该实体的面。跨平面的实体可含平面上顶点和边。移除零面积及重复面；不可修复的开放、非流形或不可定向网格退出 2，错误应能定位到边、顶点或面。
