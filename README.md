# UV 图表烘焙与 Mip

Python 3.9+，纯标准库、离线可运行。执行 `python3 bake.py demo.json output.json`。
每 chart 有 id、size（2 的幂）、origin（图集左上像素）、rotate（0、90、180 或 270）、ambient，以及 triangles。每个三角形列出 3 个 uv=[u,v]、normal=[x,y,z]。在图表局部 [0,1] 按像素中心计算重心及插值单位法线。照度为 ambient+max(0,dot(n,light))×strength，light 是全局单位方向。忽略退化 UV，允许镜像，边界约定保持稳定。无覆盖为 null。

charts[id] 含 levels 数组，每级 {size,light,owners,mask}。owners 为像素的图表 id 或 null，mask 为 cover（原始覆盖或 2×2 同图表均值）、dilate（padding 距离内的扩边）、fill（同图表私有网格的最近者补全）或 null。以最近覆盖像素扩展 padding 指定的距离，距离相同按 y,x；以 2×2 对同图表有效数据求均值。atlas 按层输出可追溯像素坐标、chart、light，禁止跨图表滤色。输入 charts 顺序、rotate、origin 不影响各图表的局部数组。顺时针 rotate=90 将局部 (x,y) 映到 (N−1−y,x)，其他类推；atlas 第 0 级必须遵守 origin/rotate。至少生成输入 levels 指定的层数（超过最大可表示层数时自动截断到 1×1）。

## 防止串色与接缝跳变的设计

1. **规范空间私有 Mip 链**（`lightmap/mips.py`）：每图表在自身 [0,1] 规范朝向上独立生成 Mip，2×2 均值只收集 owner 等于本图表的纹素。旋转打包、镜像 UV、origin 与写入次序都无法改变该链，因此不同图表在物理图集低分辨率区域的冲突永远不会污染各自的采样结果。
2. **图表隔离扩边**（`lightmap/dilate.py`）：多源 BFS 在图表私有网格内按最近覆盖纹素扩边，平局按 y 再 x；先扩 padding 契约距离，再做同图表内补全，保证各级私有链全有效，双线性采样不会触及空纹素或外来纹素。
3. **图集冲突策略显式化**（`lightmap/atlas.py`）：图集只做带 owner 的 blit，绝不做跨图表平均。同一低层纹素被多图表占用时，按最小 chart id 确定性保留胜者，败者连同层级、图集坐标与照度写入 `conflicts`。
4. **可追溯归属**（输出 `trace`）：每个纹素记录 chart、triangle、bary、normal、mirrored_uv、light、mask；`--no-trace` 可关闭。`lightmap/sample.py` 提供规范空间双线性采样、图集采样（owner 校验，外来纹素跳过而非混合）与图集纹素到图表局部坐标的逆映射。

## 模块

- `lightmap/vecmath.py`：2D 重心坐标（与绕序无关，负面积用于识别镜像）与 3D 单位法线。
- `lightmap/raster.py`：位置/法线/覆盖光栅化，共享边与重复顶点以最小三角形索引确定性裁决。
- `lightmap/mesh.py`：坡面测试网格，含共边统计、重复接缝顶点与镜像 UV。
- `lightmap/packer.py`：确定性 shelf 打包与带种子随机打包（内置 LCG，可复现，支持 90° 旋转）。
- `lightmap/bake.py`：端到端管线，输出 charts/atlas/placements/params/trace。

## 验收与演示

- `python3 run_tests.py`：A1 亮暗交错全 Mip 全采样点（线性每通道外来色与常量差 ≤1e-5）、A1b 非全覆盖亮图边缘/内部、A2 镜像坡面五次随机打包的逆映射摘要一致性与接缝差、A3 输入与写入次序无关、A4 demo 契约与旋转往返。
- `python3 demo_mesh.py`、`python3 demo_conflict.py`、`python3 demo_edge_cases.py`：镜像/法线方向、强制低层冲突不混合、退化 UV 与 level-0 契约。

