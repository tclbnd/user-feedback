# 双投影曲面校准

Python 3.9+ 纯标准库实现，无第三方依赖。运行
`python3 projection.py demo.json output.json [--digest]`。

## 输入

`pattern[y][x]` 是曲面 UV 整数网格上的线性目标亮度。`projectors` 中每台投影含
`id`、`markers`、`pixels`、`blocked`、`gain`、`black`、`gamma`，可选
`quantize`（true 时电信号按 8bit 量化）。每个 marker 含 `surface=[u,v]`、
`screen=[px,py]`。屏幕映射模型为每坐标独立的二次多项式，基为
`(1,u,v,u²,uv,v²)`。缺失 marker 可省略；每个模型至少需要 6 个满秩有效点，
否则以退出码 2 报错（退化/共线格点同样报错）。最小二乘采用 Householder QR，
在近共线的病态格点上仍保持数值稳定。

## 几何与融合遮罩

`pixels` 是每台输出图像像素的统一曲面坐标查表，可非整数，允许少量畸变。
`blocked` 是相同尺寸的布尔遮挡图。实现先对每台投影做几何标定（输出
`calibration`，按 id 排序，每项 `{id,x,y,rms}`，`x`、`y` 为 6 个系数，
`rms` 为标记的二维重投影 RMS），再把每个有效像素按最近邻落到曲面栅格。
遮挡像素与落在曲面网格之外的像素权重为零，也不产生黑位贡献。

有效区域以到该投影有效矩形边界的曼哈顿距离加 1 为初始权重（两趟 chamfer
距离变换），并在同一曲面点上跨投影归一化：独占格点权重为 1，重叠格点权重和
恒为 1。没有任何有效覆盖的格点进入 `report.invalid_cells`，`combined` 对应
位置为 `null`。单投影（全网格无重叠）时所有权重恒为 1，走单机回退，
`report.single_projector_fallback` 为 true，原始渲染目标被完整保留。

## 线性亮度补偿

给定线性目标 P，活动投影黑位和 `B = sum(black_i)`（仅统计覆盖该曲面点的
投影）。第 i 台的理想输出亮度为 `black_i + weight_i × max(0, P−B)`，电信号

```
s = clamp((light − black_i) / gain_i, 0, 1) ^ (1 / gamma_i)
```

实际亮度按正向响应链 `black_i + gain_i × s^gamma_i` 计算，`combined` 为同一
曲面点各活动投影实际亮度之和。关闭图元输出信号 0，禁用位置没有黑位贡献。
目标低于活动黑位总和时残差截断为 0，不会出现负亮度。

## 输出

`projectors=[{id,signal,weights}]`，`signal` 与 `weights` 形状与该投影
`pixels` 相同，可直接播放；`combined` 为按曲面网格相加的实际线性亮度；
`calibration` 为几何标定结果；`report` 汇总覆盖/重叠/失效格点、每台投影的
有效与遮挡像素数、单机回退标志等。加 `--digest` 时 `report.digest` 写入
对 projectors/combined/calibration 规范化序列化后的 SHA-256，同输入复跑
摘要与全部数值逐位一致。

## 验收测试

`python3 -m unittest test_projection -v`，共 15 个用例，覆盖：

- 合成标定格点独立投影位置的二次模型恢复，RMS 重投影误差 ≤ 0.5 像素；
  缺失格点仍可标定；共线/点数不足的退化模型显式报错；
- 平坦重叠 ROI 内常量灰卡的线性亮度误差：连续域 < 1e-9，8bit 量化后 < 0.02；
  遮罩在独占区为 1、重叠区归一和为 1；黑位截断与正向响应链复核；
- 单列窄重叠、曲面畸变 LUT 边缘、遮挡与越界像素的失效区域、单机回退、
  无空洞检查，以及同输入复跑的 JSON/摘要一致性和 CLI 端到端冒烟。

