# HDR 分区测光与边缘感知局部色调映射

纯标准库实现（无 numpy / scipy 依赖）。

```
python3 tm.py req.json output.json
python3 tests/run_all.py -v          # 99 项验收测试
```

## 请求

```json
{"hdr": "i.json", "tile": 16}
{"hdr": [[0.18, 0.2], [0.1, 0.05]], "zones": [4, 4], "radius": 3}
```

`hdr` 可以是内联数组，也可以是相对 req.json 的 JSON 文件路径。二维数组视为亮度；
三维数组（RGB / RGBA）按 Rec.709 亮度做色调映射，随后恢复原色度。

常用可选键：

| 键 | 默认 | 含义 |
|---|---|---|
| `tile` / `zones` | 16 / 由 tile 推导 | 分块边长（像素）/ 分块数 `[行, 列]`。两者都给时 `zones` 优先 |
| `zone_shuffle` | 0 | 非零整数种子 -> 乱序访问分区（用于验证不变性） |
| `radius` | 3 | 基底层空间半径（像素） |
| `sigma_r` | 0.35 | 基底层值域 sigma（EV） |
| `max_dark_gain` | 3.0 | **暗部增益上限**，线性，须在 [1, 3] |
| `lift_k` | 0.8 | 每低于枢轴 1 EV 的提亮量（EV），须 <= log2(max_dark_gain) |
| `ev_bias` | 0.0 | 用户曝光补偿，叠加在测光结果上 |
| `percentile` | 0.995 | 白点取自分区对数直方图的百分位 |

完整键表见 `hdr_tm/config.py`；未知键会被拒绝（`ValueError`）。

## 输出

```
ev        测光得到的曝光补偿 EV
out       逐像素显示值，[0, 1]
zone_ev   逐区投票（相对中灰的 EV）
digest    量化后的输出 sha256
stats     测光统计（整数累加量 + 导出量）
zones     逐区贡献分解
curve     色调曲线参数（分块无关）
invariant curve + digest 的联合摘要
size      [行, 列]
rgb       仅三维输入时出现
```

### 逐区贡献

`zones[i]` 含 `index / row / col / bounds / pixels / black_pixels /
black_fraction / weight / weight_sum_q / mean_ev / weight_share / vote_ev /
contribution_ev / black_zone`。

守恒关系（浮点误差 ~1e-12 内成立）：

```
sum(weight_share)    == 1
sum(contribution_ev) == log2(KEY) - mean_log_ev
```

这是"逐区贡献"可审计的原因：全局曝光恰是各区投票的加权和。

## 管线

1. **亮度 / 颜色分离** — RGB 输入取 Rec.709 亮度；颜色层保存 `(r/Y, g/Y, b/Y)`
   三元比，其亮度恒为 1，因此换任何新亮度都不改变色度。
2. **分区加权测光** — 对数域统计，每像素权重 = 中灰强调 × 高光保护 × 边界权重：
   - 纯黑像素（`Y <= 1e-6`）不投票，单独计数；取对数前先被钳到 `2^-24`，
     对数域永远见不到 0 或负无穷。
   - 中灰强调：以 `KEY` 为中心的高斯权重，避免大面积高光/暗部绑架曝光。
   - 高光保护：`clip_lo..clip_hi` 之间平滑归零，过曝样本不拉低曝光。
   - 边界权重：按**画幅**（而非分块）定义，最外圈降到 `border_floor`。
3. **曝光补偿** — `ev = clamp(log2(KEY) - Lmean + ev_bias, ev_min, ev_max)`。
4. **边缘感知基底层** — 对 `log2(Y)` 做以自身为引导的联合双边均值
   （`hdr_tm/guided.py`），得到基础层 `B` 与细节层 `D = L - B`。
5. **受限局部增益** — `lift(e) = clamp(lift_k * (pivot - e), 0, log2(max_dark_gain))`，
   只作用于基础层：`L' = B + lift(B) + D`。因而线性增益 <= 3 倍，细节层不被改动。
6. **色调曲线** — `T(u) = u(u+A)/(u(u+B)+C)`，`u = 2^e`，`e` 为相对中灰的 EV。
   `T(0)=0`、严格递增、`T(u) < 1` 对所有有限 u 成立（渐近滚降，不会削顶）。

### 为什么不分块依赖：整数累加

每像素的对数亮度量化到 2^20 网格、权重量化到 2^16 网格，各区保存**精确整数**累加量
（`sum_w / sum_wq / sum_wq2 / sum_q / n / n_black`）。全局统计是各分区整数之和，
与分组方式、访问顺序都无关（整数加法交换律）。因此：

```
stats(任意分块) == stats(任意另一种分块)        逐位相等
```

浮点只出现在逐区均值这类**按定义就依赖分区**的量上，它们从不回流到全局统计。

## 为什么无光晕

设 `w = B - L = -D` 为引导权重。对台阶边缘，邻域分裂为两个平坦簇，跨边核权重恒为
`K0 = exp(-dL^2 / 2 sigma_r^2)`。距离边缘 j 的暗侧像素 p 与其镜像 q 满足

```
B(p) = [n_d L_d + n_b K0 L_b] / [n_d + n_b K0]
B(q) = [n_b L_b + n_d K0 L_d] / [n_b + n_d K0]
```

分子共享同一窗口和，故 `D(p) = D(q)`，即 `w(p) + w(q) = 0`：两侧引导权重严格抵消。
6.3 EV 台阶上跨边核质量实测 1.3e-69（盒滤波为 91.4），暗侧偏差恰为 0.0。
作为负对照，`tests/test_halo.py` 会验证普通盒滤波在同一台阶上产生 >2 EV 的光晕。

## 验收

`python3 tests/run_all.py`（99 项，约 2 秒）：

| 要求 | 测试 |
|---|---|
| 阶梯各档重建误差 <= 0.01 | `test_curve`：-10..+18 EV 十档，实测误差 <= 2e-16 |
| 强边缘两侧引导权重和为零 | `test_halo`：镜像对抵消 + 整侧求和，非平凡台阶 |
| 无光晕指标低于阈值 | `test_halo`：3.6 / 6.3 / 15 EV 台阶两侧平坦度 |
| 两种分块 + 乱序分区结果一致 | `test_invariance`：`ev` / `curve` / `digest` 逐位相等 |
| 极暗输入无 NaN 与负值 | `test_robust`：全黑、1e-30、单点、1e9、1x1、RGB 黑 |
| 暗部增益上限 | `test_curve`：深阴影恰在上限，`2^lift <= 3` |

`out` 始终是 `[0, 1]` 内的有限浮点数；写出时用 `allow_nan=False`，NaN/Inf 无法静默落盘。

## 已知取舍

- **超色域颜色**：若色调映射后的亮度无法在全饱和下落入 `[0,1]^3`，管线保留亮度并向
  灰点去饱和。单通道裁剪会移色相，整体压暗会丢掉曝光决策，故取前者。
- **纯黑画面**：无信号可测，`ev` 置 0（不做无意义的提亮），输出为接近 0 的显示值。
- **边界权重**：画幅最外 `min(行,列)//16` 像素按 `border_floor` 降权，可用 `border` 覆盖。
- **性能**：约 9 微秒/像素（64x64 约 0.04 秒）；纯 Python 的双边核为 O(n * (2R+1)^2)。
