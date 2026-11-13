# 透明片段分块合成

Python 3.9+，纯标准库。执行 `python3 composite.py demo.json out.json --tile 4 --budget 16`。
场景有 size=[宽,高]、samples（每像素样本数）和 opaque[y][x][sample]。每个不透明样本给出 z（null 表示没有遮挡）及线性预乘 rgba（可为透明背景）。透明输入 fragments 是列表，每条含 x、y、sample、z、id（稳定全局整数）及线性预乘 rgba。数据生成后只读，视为源缓存，可按需反复扫描。z 越小越近。z 大于或等于不透明 z 的片段被遮挡，零 alpha 不参与合成。

## 合成管线（composite.py）

1. **逐样本遮挡与透明/不透明交叉**：遮挡测试以样本为单位，用该样本自己的不透明 z 比较；同一透明物可在部分样本上位于不透明表面之前（可见）、在另一些样本上位于其后（被剔除），即透明物体可交叉穿过不透明表面。
2. **稳定顺序**：每条样本射线按 `(z, id)` 由近到远排序。`id` 是与提交顺序无关的内在整数，同深度片段因此具有确定次序，两种提交顺序产出逐位一致的结果。
3. **有界存储路径**：按方形 tile（`--tile`）扫描只读源，轻量索引（片段下标、计数）不计入片段记录预算。预算内样本的记录进入排序缓存，缓存按批 flush，**同时驻留的透明片段记录数硬性不超过 `--budget`**。
4. **准确回退**：有效片段数超过 budget 的样本不截断、不丢片段，改用多次源扫描的 rank 选择回退——第 k 次扫描按键 `(z,id)` 选出第 k 小记录，每次只驻留 1 条记录，再走同一套解析 OVER 数学。受影响像素写入 `fallback_pixels`，包围盒写入 `fallback_region`，回退样本数写入 `fallback_invocations`。
5. **MSAA resolve 与色彩变换分离**：线性预乘空间内逐样本前→后 OVER 合成（alpha==1 即终止遮挡其后内容；极低 alpha 不做 epsilon 裁剪），resolve 取各样本四通道算术均值；gamma 编码是 resolve 之后的独立步骤（`--gamma`），不修改 alpha 通道。

`stats` 含 `overflow_pixels`（存在样本有效片段数超过 budget 的像素数）、`peak_fragment_records`（合成路径实际同时驻留记录数，始终 ≤ budget）、`fallback_pixels`/`fallback_region`/`fallback_invocations`，以及零 alpha、被遮挡、有效片段计数和源扫描次数，全部独立于提交顺序。预算及 tile 必须为正整数，否则抛 `ValueError`。

## 独立参考与验收

- `render_reference(scene, gamma=None)`：独立的无预算、无 tile 参考实现，逐射线 `(z,id)` 排序后做后→前解析 OVER，不与主路径共享累加代码，作为误差对照 oracle；CLI 加 `--reference` 可单独运行。
- `python3 test_oit.py`：确定性场景（LCG，无第三方依赖），覆盖重叠薄片、同深度带稳定序号片段、预算 8 倍（48 条/射线）深射线、透明物穿过不透明表面、alpha 为 0/1/1e-7 的边界；按**两种提交顺序 × 两种分块大小**复跑，与参考比较 RGBA（容差 2e-6，实测误差 ~1e-16），比较溢出统计的逐位一致性，并用独立的驻留模拟器断言峰值驻留不超过预算，报告回退像素数与区域。
- `python3 stress_check.py`：budget=1 全量回退压力、参数校验、gamma 通道保持、零 alpha 射线不触发回退。

