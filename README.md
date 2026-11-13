# 遮挡感知运动补偿插帧

Python 3.9+，标准库。`python3 memc.py request.json outdir`

request.json：`{"frames":[{"file":"f0.pgm"},{"file":"f1.pgm"}],"t":[0.5],"block":8}`。帧为 PGM(P2) 灰度图。对每对相邻帧在时刻 t（0<t<1）插值。

算法契约：

- **双向块匹配**：对每个输出块，在 frame0/frame1 上搜索位移 (dx,dy)，前向流 f01 与反向流 f10。块大小 `block`，搜索半径 ±8，SAD 准则。块匹配的计算分块/遍历顺序**不得影响结果**。
- **一致性**：对每个像素检查 `f01(p) ≈ -f10(p+f01(p))`（容差 1 像素）。不一致 → 遮挡标记。
- **合成**：非遮挡像素 `I_t(p) = (1-t)·I0(p - t·f01) + t·I1(p + (1-t)·f10)`；遮挡像素使用单向补偿（取可见侧，即一致性失败时若反向有效则用 `I1(p + (1-t)·f10)`，否则用 `I0`）；两侧皆无效回退帧复制 `I0(p)`。
- 输出每个 t 一张 `interp_{t}.pgm`，以及 `stats.json`：`{"occl_ratio":float,"fallback_px":int,"mv_count":int}`。

现有实现缺陷：只做线性混合 `I_t = (1-t)I0 + t·I1`（无运动补偿），无遮挡检测——验收会全灭。夹具自带 `make_scene.py` 生成合成平移/遮挡帧。
