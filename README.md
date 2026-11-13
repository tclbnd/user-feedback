# 离线光谱路径回放

Python 3.9+，只使用标准库。普通渲染：

```bash
python3 spectral.py demo.json out.json --tile 4 --workers 1
```

内置验收与报告生成：

```bash
python3 spectral.py --validate --outdir reports --workers 2
```

## 场景格式

- `wavelengths`：离散波长（nm）。
- `wavelength_pdf`：可选，波长提议概率，必须归一化；默认均匀。
- `wavelength_weights`：可选，目标光谱积分的求积权重，必须非负且和为 1；默认均匀。
- `xyz_basis`：每个波长的线性 XYZ 三刺激值，长度与 `wavelengths` 相同。
- `materials`：名称映射到逐波长正折射率 `ior` 与非负吸收系数 `absorb`；空气 IOR=1。
- `spp=0`：对所有波长和光源做确定性全枚举，同时作为 Monte Carlo 的逐像素参考。
- `spp>0`：用 `seed:pixel_index:sample_index` 构造独立 RNG；分块、线程数和完成顺序不进入种子。

### 路径事件

`rays[].events` 是有序离线事件，不做几何求交：

- `enter`：按界面 `normal` 做 Snell 折射，成功透射后把介质压栈。
- `exit`：弹出栈顶介质；若材质与栈顶不匹配会报错。
- `distance`：按当前介质和传播距离计算 Beer–Lambert 透射率 `exp(-mu*length)`，并更新 2D 位置；默认直接求期望，也可设置 `sample_transmission=true` 与正的 `sample_rate`，在“段内吸收碰撞（权重 0）/段末存活”两类透射事件上采样。
- `scatter`：可给归一化分支 `pdf` 与真实分支 `weight`；随机采样时计入联合概率和权重。
- 界面默认直接报告 Fresnel 而不随机分支；设置 `sample_fresnel=true` 后会按反射/透射 PDF 采样，并把该 PDF 纳入统一联合权重。
- 全反射（TIR）：输出反射角、`fresnel=1`、递增 `tir_count`，介质栈保持不变。
- 嵌套玻璃：栈记录父介质，进入/退出时在父介质和目标介质的逐波长 IOR 之间做 Snell。
- 每个界面同时报告入射角、折射角和非偏振 Fresnel；默认透射率仍只包含吸收，界面 Fresnel 单独报告，避免把界面能量和体吸收重复相乘。

### 光源与统一权重

`lights[]` 包含逐波长 `spectrum`、真实权重 `weight` 和提议概率 `pdf`。所有光源 PDF 必须和为 1。随机路径的联合口径为：

```text
p_joint = p_wavelength * p_light * p_scatter * p_transmission
```

普通距离求期望和直接 Fresnel 报告的对应提议 PDF 为 1；启用距离或 Fresnel 采样后，`rays[].event_log` 与 `sampling` 中会保留实际分支 PDF，并计入同一个 `p_transmission`。各随机因子按 `weight / pdf` 进入路径吞吐：散射、Fresnel、透射距离分支与光源均使用这一统一口径；波长再用目标求积权重除以 `p_wavelength`。

## 输出

- `rays[].transmission`：逐波长体透射率；默认是 Beer–Lambert 物理值，启用透射事件采样后为已按 PDF 加权的估计值，原始物理量保存在 `event_log[].physical_transmission`。
- `rays[].exit_angles` / `positions`：逐波长出射角与 2D 位置。
- `rays[].tir`、`tir_count`、`final_stack`、`event_log`：TIR、栈和逐事件诊断。
- `rays[].reference`：确定性全枚举参考。
- `rays[].stats`：Welford 方差、标准误差、相对噪声，以及相对参考的能量/XYZ 偏差。
- `image_xyz` / `image`：线性 XYZ 缓冲；`image_srgb`：仅显示阶段做线性 XYZ→sRGB 和钳制。
- `buffer_digest`：线性 XYZ 缓冲的 SHA-256 摘要，用于比较不同分块顺序。

## 内置验收内容

`--validate` 自动生成以下报告到输出目录：

1. 均匀介质与单次折射：相对透射率误差阈值 1%，出射角绝对误差阈值 0.5°。
2. TIR 与嵌套玻璃：验证反射、栈不回退、嵌套层吸收和最终闭栈。
3. 漫反射盒 ROI：256 SPP 与独立 4096 SPP 高样本基准比较线性 XYZ 均值，绝对误差阈值 0.02。
4. 棱镜：固定种子 `101/202/303/404`，检验 430nm 与 680nm 出射位置，输出：
   - `prism_spectral_seed_*.ppm`：光谱积分显示图；
   - `prism_spectral_reference.ppm`：确定性参考图；
   - `prism_difference_seed_*.pgm` 和 `.json`：线性 XYZ 差异图及数值；
   - `prism_tile_order_summary.json`：正序/逆序分块的缓冲摘要；
   - `energy_stats.json` 与 `validation_summary.json`：逐场景能量偏差、噪声和全部阈值检查。

受限沙箱若不允许进程池信号量，渲染器会自动退回同构的串行执行；数学结果和缓冲摘要不变。
