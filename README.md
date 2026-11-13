# 离线光谱路径回放

纯 Python 3.9+ 标准库实现（无 numpy / Pillow 依赖）。面向产品摄影在玻璃、
吸收介质与有色光源下需要稳定色散结果的场景，把波长、介质边界、吸收与各类
采样概率统一到同一套光谱估计器中，避免 RGB 管线把不同波长能量混在一起。

## 运行

```
python3 spectral.py demo.json out.json --tile 4 --workers 1
python3 spectral.py scenes/nested_glass.json out/nested.json --tile 1 --workers 2
python3 validate.py            # 全部解析/统计验收，产物写入 out/
```

可选 `--tile-order 1,0` 用指定排列交换分块处理顺序，用于验证调度无关性。

## 模块

- `spectralrng.py`：基于 SplitMix64 的计数器式 RNG。每次抽样是
  `(pixel, sample, channel, seed)` 的纯函数，波长/光源/传输/散射各占独立
  通道链，没有会被线程或分块顺序扰动的共享可变状态。
- `spectra.py`：波长网格、梯形积分权重、XYZ 观测基（缺省时用高斯伪观测基），
  以及线性 XYZ → 线性 sRGB → sRGB 编码的显示转换。
- `medium.py`：介质边界栈、按波长的有符号 Snell 折射、全反射（TIR）反射、
  非偏振 Fresnel 与 Beer–Lambert 吸收。
- `geometry.py`：验收用的 2D 多边形/棱镜显式求交（与离线事件回放相互独立的
  几何实现，用于交叉核对）。
- `renderer.py`：场景装载、统一权重估计器、枚举参考、分块线程调度、Welford
  在线统计与缓冲摘要。
- `imaging.py`：零依赖 8-bit PNG 编码、光谱彩带图与差异图。
- `validate.py`：五项验收及 `out/acceptance.json` 汇总。

## 事件与介质栈语义

`wavelengths`（nm）与 `xyz_basis`（每波长三分量）等长。`materials` 为名称到
`{ior: [每波长正折射率], absorb: [每波长非负系数], scatter?: [每波长非负系数]}`，
空气 IOR=1。

`rays[].events` 为有序事件：

- `enter` 把指定介质压栈；`exit` 弹出栈顶并回到下一层（材质不匹配或缺栈时报错）。
- 角度先相对事件 `normal`（有符号参考法线角，度）做 Snell；透射更新全局角度。
- 检测到全反射时反射光线、**栈保持原样**并记录事件下标与 Fresnel。
- `distance` 按当前介质吸收系数做 Beer–Lambert `exp(-sigma_a L)`。
- 界面 Fresnel 单独报告，`transmission` 只计吸收，不重复计入界面反射。

嵌套玻璃通过栈顶介质与下一介质的 IOR 比值逐波长处理；平行面出射会还原入射
方向，倾斜面按各自法线折射。

## 统一权重口径

三类采样决策放进同一个联合提议概率：

```
p_joint = p_wavelength · p_light · p_transport · p_scatter
```

- 波长提议：默认网格均匀（可用 `wavelength_pdf` 覆盖）。
- 光源提议：面积光 `pdf` 字段（必须和为 1），真实分支权重用 `weight`。
- 传输提议：
  - `analytic`（默认）：确定性距离回放，`p_t = 1`，吸收由 Beer 项承载；
  - `freeflight`：纯吸收介质用 delta tracking（首次真实碰撞即终止），含散射
    介质用 ratio tracking，其权重期望严格等于 `exp(-sigma_a L)`。
- 散射提议：竞争事件概率，与传输共用联合分母。

每个 MC 样本用「真实光谱贡献 / 联合 pdf / 波长数」作为权重，网格间距权重承载
积分测度。`spp = 0` 是确定性全枚举零方差参考；MC 结果相对它分别报告能量偏差
（`bias`、`mean_abs_xyz_bias`、`mean_energy_bias`）与噪声
（`variance`、`standard_error`、`rms_standard_error`）。

## 输出

每个像素保留 `transmission`、`exit_angles`、`tir`、`tir_events`、`fresnel`、
`stack`、线性 `xyz`、`reference_xyz`、`linear_srgb` 与编码后的 `display_srgb`。
顶层 `image` 是全体线性 XYZ，`digest` 是其规范化 JSON 的 SHA-256。同一 `seed`
下，改变 `--tile`、`--workers` 或分块顺序，逐像素抽样序列与 `digest` 完全一致；
改变 `seed` 才会改变序列。

## 验收结果（`python3 validate.py`）

1. 均匀介质 Beer–Lambert 透射率与独立解析值相对误差为 0（阈值 1%）；单次折射
   出射角对独立 Snell 参考的最大绝对误差为 0（阈值 0.5°），并显式校验临界角外
   的 TIR 反射。漫反射盒 ROI 线性颜色均值对独立高样本基准的最大绝对误差约
   4e-4（阈值 0.02）。
2. 4 个固定种子（11/23/37/53）下棱镜长短波出射位置严格单调、几何结果跨种子
   完全一致；输出 `out/prism_spectral.png`（光谱积分图）、
   `out/prism_difference.png`（差异图）与 `out/prism_energy.json`（逐场景能量
   统计，偏差/噪声分列）。交换分块顺序与线程数后缓冲 `digest` 一致。

测试文件 `t_medium.py`、`t_geometry.py`、`t_renderer.py` 为可独立运行的单元回归。

