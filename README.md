# 解析线段覆盖抗锯齿
`python3 aa.py req.json output.json`
req.json: `{"segs":[{x0,y0,x1,y1,w, rgba?}],"size":64,"tile":32, "order"?, "cap"?, "join"?, "miter_limit"?}`

## 方法
逐像素**解析覆盖** = 像素矩形 `[ix,ix+1]×[iy,iy+1]` 与笔划图元的相交面积,
覆盖值作为 alpha 进入预乘 (premultiplied) src-over 合成, 无点采样, 亚像素宽不消失.
- 矩形图元: Sutherland-Hodgman 半平面裁剪 + 鞋带公式求交面积
- 圆盘/圆扇形: Green 定理边积分 (圆内直线三角形项 + 圆外 atan2 圆弧项)
- 水平/垂直/任意角/近退化(零长度→圆盘, 极短段)统一处理

## 折线分解 (共享端点不重复计覆盖)
相邻段按共享端点自动串链, 分解为内部互不重叠的图元:
- 段矩形在公共顶点沿角平分半平面切割, 两端各归一侧
- 内角缺口由连接样式填充: `round`=圆扇形(张角|转角|), `bevel`=三角, `miter`=斜接四边形(超限回退 bevel)
- 自由端帽: `butt`=平切 / `round`=半圆盘 / `square`=矩形外延半宽
- 不同链的相交统一 clamp, 每像素覆盖恒 ≤ 1

## 边界与分块约定
- 像素闭约定 `[ix,ix+1]×[iy,iy+1]`, y 轴向上; 画布 `[0,size]²`, 部分出屏按同一裁剪约定
- 整数平移几何一致; 分块只决定调度, 每像素按全局固定图元顺序 `math.fsum` 累加
- `tile` 任意分块大小 × `order`(raster/column) 两种遍历顺序, RGBA 逐像素严格一致

## 输出
`cov`(size×size 浮点覆盖 0..1), `rgba`(预乘 8-bit), `energy`(覆盖总和), `digest`(RGBA 的 sha256)

## 验收测试
`python3 test_aa.py` — 几何原语解析面积、K=64/128 supersample 参考逐像素误差 ≤ 1/64、
亚像素宽总能量==解析长度×宽(+端帽)、两种 tile×两种顺序逐像素一致、交点覆盖≤1、
整数平移/出屏/退化段、端帽与连接样式、预乘合成. 纯标准库, 无第三方依赖.

