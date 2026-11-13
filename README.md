# UV 图表烘焙与 Mip

Python 3.9+。执行 `python3 bake.py demo.json output.json`。每 chart 有 id、size（2 的幂）、origin（图集左上像素）、rotate（0、90、180 或 270）、ambient，以及 triangles。每个三角形列出 3 个 uv=[u,v]、normal=[x,y,z]。在图表局部 [0,1] 按像素中心计算重心及插值单位法线。照度为 ambient+max(0,dot(n,light))×strength，light 是全局单位方向。忽略退化 UV，允许镜像，边界约定保持稳定。无覆盖为 null。

charts[id] 含 levels 数组，每级 {size,light,owners}。owners 为像素的图表 id 或 null。以最近覆盖像素扩展 padding 指定的距离，距离相同按 y,x；以 2×2 对同图表有效数据求均值。原覆盖值和扩展值可额外标记。每级可独立重新打包，也可为碰撞保留分层记录。atlas 按层输出可追溯像素坐标、chart、light，禁止跨图表滤色。输入 charts 顺序、rotate、origin 不影响各图表的局部数组。顺时针 rotate=90 将局部 (x,y) 映到 (N−1−y,x)，其他类推；atlas 第 0 级必须遵守 origin/rotate。至少生成输入 levels 指定的层数。
