# 延迟不透明+前向透明混合管线
`python3 pipe.py req.json output.json`
req.json: `{"opaque":[{z,albedo,normal?}],"trans":[{z,alpha,tint,normal?}],"lights":[{dir,color,intensity?}],"orders":3}`

## 通道分离
- 不透明通道只将最近不透明片元写入 G-Buffer（深度、albedo、normal、索引）。
- 透明前向通道的 `transparent_writes=0`，不写 G-Buffer，只读取深度；`z > opaque.z` 的透明片元被深度剔除，等深片元保留，避免接触边界错误剔除。
- 透明层按确定性深度顺序（远到近）做 premultiplied over，同深度使用材质和稳定索引决胜，提交顺序变化不会改变结果。

## 统一光照与色彩
- 两条通道调用同一个 Lambert 模型：`max(0, dot(normalize(normal), normalize(light.dir))) * baseColor * lightColor * intensity`。
- 输入与输出均为线性色彩；不透明 albedo 和透明 tint 走同一材质光照函数。
- 不透明与透明的无光照背景默认黑色；有不透明表面时最终 alpha 为 1，否则 alpha 为透明累计覆盖率。

## 输出
`color` 为线性 RGBA；`per_light` 为逐光源最终 RGB；同时输出：
- `opaque_per_light`：延迟不透明逐光贡献；
- `transparent_per_light`：透明自身 premultiplied 逐光贡献；
- `transparent_net_per_light`：相对不透明基线的净透明贡献；
- `occlusion`/`occ`：不透明 G-Buffer 与透明深度剔除统计；
- `gbuffer`：G-Buffer 内容及透明零写入标记；
- `order_check`：3 组（或 `orders` 指定组数）透明提交顺序、digest、RGBA/逐光源最大差值；
- `ray_reference` 与 `validation`：独立逐射线参考、逐光源/RGBA 误差和遮挡索引校验，验收阈值 `0.01`。
