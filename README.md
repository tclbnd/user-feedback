# 环境贴图的球谐 irradiance 投影与系数截断

漫反射 IBL 直接按纹素查询在粗糙表面上会出现方向性条纹;本项目用三阶球谐(9 系数/通道)
的低频近似替代,并提供可乱序重放的分块投影与余弦卷积 irradiance 重建。

## 文件

- `envmap.py` —— 缺陷基线(保留不动):等纹素加权(忽略 sinθ 两极拉伸)、irradiance 无 A_l 卷积。
- `sh_irradiance.py` —— 修正实现(CLI 契约与基线一致):
  `python3 sh_irradiance.py input.json output.json`
- `verify_sh.py` —— 验收脚本:`python3 verify_sh.py`(纯标准库,无需 numpy)。

## input.json

`{"scene":{"type":"uniform|dirlight|gradient|patches",...},"w":32,"h":16,
  "blocks":4,"order":"sequential|shuffle","subsamples":12,"hdr_clamp":null,
  "dirs":[[theta,phi]...],"seed":1}`

场景契约(解析定义,θ∈[0,π) φ∈[0,2π)):
- uniform: 常亮 `v`
- dirlight: `dir=[θ,φ]` 上半径 0.05 rad 的圆盘,强度 `i`(标量或 RGB)
- gradient: `lo + (hi-lo)·(0.5+0.5·cosθ)`
- patches: `rects=[[θ0,θ1,φ0,φ1,v]...]`,其余 `bg`;`v/bg` 可为 RGB 三元组

## 模型约定(修正版)

- 三阶实 SH(9 系数,RGB 场景为 3×9),Condon-Shortley 约定:
  `c_lm = Σ_texel L·Y_lm(dir)·dΩ`,纹素立体角 `dΩ = sinθ·Δθ·Δφ`(两极拉伸正确加权)。
- 方向光圆盘的跨边界纹素做 `subsamples×subsamples` 子采样估计平均辐亮度,立体角只计一次。
- irradiance 重建做 SH 余弦卷积,逐带缩放 `A0=π, A1=2π/3, A2=π/4`:
  `E(n) = Σ_lm c_lm·A_l·Y_lm(n)`。
- 投影按 `blocks` 分块:块内普通求和、块间 `math.fsum` 归约;`shuffle` 用固定种子确定性乱序,
  块数/顺序不改变系数(L2 摘要 sha1 一致)。
- `hdr_clamp` 对辐亮度逐通道截断后再投影。

## 输出

`{"coeffs":[9] 或 [3][9],"irradiance":[逐 dir],"block_count":n,"order":...,
  "digest":"sha1(6 位小系数化)","stats":{逐带 L2 + irradiance min/max/mean},
  "per_dir":[{"dir","irradiance"}...]}`

## 验收结果(verify_sh.py,19 项全过)

- 解析系数 vs 闭式解:uniform/gradient L2 相对误差 0.002%,dirlight 圆盘 0.39%(均 ≤2%)。
- 渐变 irradiance vs 解析解误差 0.005%;HDR 截断后 1024 方向 vs MC 均值误差 3.0%(≤5%)。
- 2/4/8 分块 × 顺序/乱序,系数 L2 差异 ~1e-14、摘要完全一致。
- 1024 方向 vs 8192 样本余弦加权 MC:低频场景逐点 ≤1%(限 3%);MC 自噪声 <0.4%。
- 基线对照:均匀光基线 irradiance 偏离 π(缺陷确认),修正版细网格误差 <0.01%。
- 高频锐利 patch 的逐点峰值受三阶截断振铃限制(均值仍 ≤3%),属低频近似的预期行为。

## 缺陷基线(仅供对照)

`envmap.py`:等纹素加权(无 sin(theta) 立体角)、irradiance 直接求和无 A_l 卷积缩放。

