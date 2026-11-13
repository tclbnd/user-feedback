# 复合轮廓距离场

Python 3.9+，执行 `python3 glyph.py demo.json output.json`。glyphs 为 {id,components} 列表。component 含 transform=[a,b,c,d,tx,ty]（按 (ax+by+tx,cx+dy+ty)）、paths（轮廓命令列表，每项 M、L、Q、Z）。局部坐标常在单位方形内，给定 64 texels/em、padding=5。轮廓以 nonzero 填充；镜像会改变绕向。Q 指二次 Bézier 控制点和终点。隐式闭合路径。构造以 em 为单位的带符号距离，内部负、外部正。图集块必须互不重叠，边距足够。输出 glyphs[id].atlas_rect=[x,y,w,h]，atlas 为行优先的距离网格，可选额外信息。

renders 每项有 id、glyph、width、height、scale（像素/em）、origin（em 原点在画面中的像素坐标）、stroke（em，默认 0）。像素中心映回 em 后，基于该图集项的距离场采样，用一像素过渡求覆盖，半透明边缘保留。stroke>0 作为外扩。返回 images 与 renders 同顺序，每项 id 和 coverage。交换 glyphs 顺序不影响指定字形的最终图。不得从相邻图集项读取。参考层级为 18–96 像素/em。
