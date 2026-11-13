# 逐样本矢量路径光栅化

Python 3.9+，用 `python3 vectors.py demo.json output.json --tile 4`。输入含 size=[宽,高]、可选 origin=[x,y]、shape、clips、color。shape 及每个 clip 均含 rule（nonzero 或 evenodd）和 commands。命令为 ["M",x,y]、["L",x,y]、["Q",cx,cy,x,y]、["C",c1x,c1y,c2x,c2y,x,y]、["Z"]。新的 M、Z 或结束时隐式闭合上一子路径。重复端点、水平边和零面积轮廓允许。坐标为世界像素单位；origin 为视口左上角。为避免大坐标误差，可先减 origin。

每像素取 4×4 样本，按 y、x 顺序，局部偏移为 (i+0.5)/4。水平向右发射射线；根采用下端包含上端排除，只有交点 x 严格大于样本 x 时计数，上行加 1，下行减 1；相切的两条根抵销。应用 shape 规则后对每个 clip 的覆盖做交集。输出 mask[y][x] 是 16 位掩码，bit=4×样本行+样本列。color 是线性预乘 RGBA，rgba[y][x] 四通道等于 color×有效样本数/16。tile 只影响处理块尺寸。用 JSON 掩码作为可机器对照的覆盖图，提供至少一个复跑命令。
