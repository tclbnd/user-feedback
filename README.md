# 逐样本矢量路径光栅化

纯 Python 3.9+ 标准库实现（无 numpy / PIL）。前端入口保持不变：

    python3 vectors.py demo.json output.json --tile 4
    python3 vectors.py demo.json output.json --tile 4 --reference \
        --pgm cov.pgm --ppm rgb.ppm

输入含 `size=[宽,高]`、可选 `origin=[x,y]`、`shape`、`clips`、`color`。`shape`
及每个 clip 均含 `rule`（`nonzero` 或 `evenodd`）和 `commands`。命令为
`["M",x,y]`、`["L",x,y]`、`["Q",cx,cy,x,y]`、`["C",c1x,c1y,c2x,c2y,x,y]`、
`["Z"]`。新的 `M`、`Z` 或命令流结束都会隐式闭合上一子路径。重复端点、重复
控制点、水平边和零面积轮廓均允许。坐标是世界像素单位；`origin` 为视口左上角，
在任何几何运算前一次性精确减去（对整数平移是 double 精确的），因此大坐标加
整数平移与对应的小坐标场景逐像素相同。

## 模块

- `rsample/geometry.py`：坐标平移；二次/三次贝塞尔按 de Casteljau 自适应细分
  扁平化为有向线段（含重复控制点退化处理、隐式闭合、零长度段丢弃）；统一的
  射线交点与符号约定。
- `rsample/coverage.py`：生产用分块扫描光栅器 `rasterize_tiled`，以及结构完全
  独立、逐样本重新发射射线的绕组参考实现 `rasterize_reference`；规则应用、多层
  裁剪逐样本交集、预乘 RGBA 合成。
- `rsample/imageio.py`：纯标准库 PGM/PPM 写出（覆盖图、白底 RGBA、掩码差分与
  RGBA 差分）。
- `vectors.py`：CLI 前端，输出与基线兼容的 JSON（`mask` 与 `rgba`）。

## 统一约定（两引擎必须逐位一致）

- 像素 (x,y) 覆盖半开方块 [x,x+1) x [y,y+1)；每像素 4x4 样本，样本位于
  x+(j+0.5)/4、y+(i+0.5)/4。
- 每个样本向 +x 发射水平射线；边采用下端包含、上端排除，仅当交点 x 严格大于
  样本 x 才计数；上行 +1、下行 -1，相切的两条根相互抵销。交点恰好落在样本上
  不计数（严格大于），扫描器用排序交点 + `bisect_right` 后缀绕组实现同一语义。
- `nonzero`：绕组非零即覆盖；`evenodd`：绕组奇偶为奇才覆盖。
- 多层裁剪在**同一套逐样本坐标**下对 16 位掩码按位与（覆盖指示函数之积），
  任一层透明的样本保持透明，保证预乘色合成一致。
- 分块只划分工作量：所有交点与样本坐标都在全局坐标系计算。块为半开
  [x0,x1) x [y0,y1)，相邻块裁边、整数平移、部分出屏（含大于画面的块）共用同一
  边界约定，因此改 `--tile` 不改变任何一位。
- `mask[y][x]` 为 16 位掩码，bit = 4*样本行 + 样本列；`rgba[y][x]` 四通道等于
  `color * 有效样本数 / 16`（线性预乘），透明像素为全零。

## 验收

    python3 tests/render_acceptance.py     # 21 项检查，退出码 0 即全过
    python3 tests/random_differential.py   # 固定种子的 80 个随机场景差分测试

验收覆盖：无自交与自交合成路径（含真实 winding-2 区域以区分两种规则）、重合
轮廓、共切曲线、一像素内窄缝与 0.8px 薄边、嵌套裁剪、隐式闭合、重复控制点与
重复轮廓、零面积轮廓、部分出屏、大坐标整数平移。每个场景在 `tests/output/`
输出覆盖图 `*_cov.pgm`、对参考引擎的掩码差分 `*_refdiff.pgm`、两种分块大小的
掩码差分 `*_tilediff.pgm`、白底 RGBA `*_rgb.ppm` 与 RGBA 差分
`*_rgbadiff.ppm`，并汇总 `report.json`。同一脚本以 tile=3 与 tile=7（另含
tile=1 与大于画面的 tile=64）渲染，RGBA 逐像素相同；生产引擎与独立绕组参考
引擎逐位相同（XOR 为 0）。
