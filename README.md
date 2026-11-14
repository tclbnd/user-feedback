# YUV↔RGB 色彩转换修复

Python 3.9+，仅标准库。`python3 convert.py input.json output.json` 做批量转换，
`python3 convert.py --selftest` 跑验收自检，`python3 convert.py --diagnose bt601 bt709`
打印两代标准的常量差异。

## 问题复现与根因

交付端症状是**暗部发灰**加**高饱和偏色**。两个症状来自三个互相独立的缺陷，都在旧
`convert.py` 里：

| 缺陷 | 旧代码 | 后果 |
|---|---|---|
| 范围缩放顺序反了 | limited↔full 的缩放放在矩阵**之前**，且作用在 YUV 端 | 黑电平被抬高、色度以错误中心压缩 → 暗部发灰 |
| 矩阵常量混用两代标准 | `K["bt709"]` 实际是 `(0.299, 0.114)`，即 BT.601 | 绿色/青色饱和度与色相整体偏移 |
| 色度增益公式符号错 | `0.5/(1+Kb)`、`0.5/(1+Kr)` | 色度幅度被压缩 → 高饱和色变淡偏色 |

正确的顺序（本实现强制）：

```
RGB code ──按自身范围归一化──► R'G'B' ∈ [0,1]
        ──色彩矩阵─────────► Y' ∈ [0,1], Cb/Cr ∈ [-0.5,0.5]
        ──按 YUV 范围缩放───► Y'CbCr
        ──四舍五入 + 截断───► codes
```

反向严格镜像。范围缩放**永不**先于矩阵，也永远不作用在另一端的采样值上。

## 输入 / 输出

```json
{"standard": "bt601"|"bt709"|"bt2020",
 "rgb_range": "full"|"limited",
 "yuv_range": "full"|"limited",
 "path": "matrix"|"lut"|"both",
 "chroma_layout": "4:4:4"|"4:2:2"|"4:2:0"|"4:2:0-left"|"4:2:2-left",
 "trace": true,
 "trace_limit": 1000,
 "roundtrip": true,
 "meta": {"standard": "bt709"},
 "chain": [{"standard": "bt601", "direction": "rgb-to-yuv"}],
 "ops": [{"direction": "rgb-to-yuv"|"yuv-to-rgb",
          "standard": "bt709",
          "rgb_range": "...", "yuv_range": "...",
          "pixels": [[a,b,c]],
          "roundtrip": true, "trace": true}]}
```

除 `standard`（可默认 bt709）、`rgb_range`、`yuv_range`、`ops` 外均为可选。
`path` 默认 `matrix`；`chroma_layout` 默认 `4:4:4`。

输出：

```json
{"ops": [{"pixels": [[...]],
          "warnings": ["mixed-standard"],
          "range_tags": {"standard": "bt709", "rgb_range": "full",
                         "yuv_range": "limited", "layout": "4:4:4",
                         "chroma_phase": [0.0, 0.0], "path": "matrix"},
          "roundtrip": {"max_err": 1, "tolerance": 2.0, "ok": true,
                        "clipped": [], "over_tolerance": []},
          "trace": [{"index": 0, "src": [0, 0, 0], "dst": [16, 128, 128],
                     "raw": [16.0, 128.0, 128.0],
                     "clipped": [false, false, false],
                     "tags": {"...": "..."}, "op": 0}]}],
 "warnings": ["mixed-standard"],
 "trace": {"records": [], "summary": {"pixels": 0, "ops": 1}},
 "consistency": {"identical": true, "max_delta": 0, "comparisons": []},
 "conventions": {"standard": "bt709", "rgb_range": "full",
                 "yuv_range": "limited", "rounding": "half-up"}}
```

`warnings` 是稳定的机器可读 token，人话解释在 `yuvkit.request.WARNING_TEXT` /
`yuvkit.checks.explain()`：

- `mixed-standard` — 请求 / 链路 / per-op 元数据里出现两代标准（601/709 混用）
- `mixed-range` — 链路在 full 与 limited 之间跳转却没有显式转换步骤
- `range-mismatch` — 码值与声明范围矛盾（full 码值出现在 limited 声明下，或反之）
- `clipping` — 有采样点触到码值边界
- `roundtrip-over-tolerance` — 往返超出 2/255 预算

请求层的越界码值会被截断并标 `range-mismatch`，而不是整单失败；需要严格模式时直接用
`Converter.convert(..., clamp=False)`（默认），它会抛 `ConversionError`。

## 常量与顺序

矩阵全程用 `Fraction` / 整数有理数，`DEN = 10000`：

- `bt601` Kr=0.299 Kb=0.114；`bt709` Kr=0.2126 Kb=0.0722；
  `bt2020` Kr=0.2627 Kb=0.0593；`Kg = 1 - Kr - Kb` 由归一化推出。
- `Cb = (B'-Y') / (2(1-Kb))`，`Cr = (R'-Y') / (2(1-Kr))`，增益由**同一组**
  Kr/Kb 导出。
- `ColorMatrix.validate()` 逐项断言 `Kr+Kg+Kb=1`、增益等于 `1/(2(1-K))`、
  权重确实代表该标准。任何"借来"的常量都过不了这一关（有对应单测）。

四舍五入是 `floor(v + 1/2)`（半值向上），全程整数运算，不受宿主 FPU 影响；
截断在舍入判定之后，作用于声明范围的端点。

## 范围与无损通道

| | full | limited |
|---|---|---|
| Y' | 0..255 | 16..235 |
| Cb/Cr | 0..255（零色度在 128） | 16..240（零色度在 128） |
| R'G'B' | 0..255 | 16..235 |

`yuvkit.lossless` 用**解析式**给出每个通道的往返上界（从实际常量推导，不是按标准
硬编码数字），因此"无损声称"与矩阵常量不会各自漂移：

- `RGB→YUV→RGB`：损失只来自 Y/Cb/Cr 的 1/2 码舍入；G 通道因为
  `Y' - (Kr/Kg)(R'-Y') - (Kb/Kg)(B'-Y')` 中亮度项相消，界最紧。
- `YUV→RGB→YUV`：只对**色域内**三元组成立。色域外的第一段就会被截断，任何反向
  变换都救不回来——这正是每个像素都带 `clipped` 标记而不是"看起来还行"的原因。

截断规则（`yuvkit.lossless.clipping_rules()` 可机读导出）：

- 中间值超出声明范围 → 截到该端点，标 `clipped-<通道>`，并保留未舍入真值；
- 负的 R'G'B' 中间值 → 截到范围下限，额外标 `negative-<通道>`，
  让"内容问题"在截断之后仍然可见；
- 舍入在截断判定之后进行，"clipped" 永远是对数学结果说的。

## 色度二次采样与相位

`yuvkit.chroma` 把**前向子采样**与它的**逆**放在同一处，相位只定义一次：

- `left`（co-sited，MPEG-2 水平相位 0）：下采样为 2 抽头平均，重建为线性插值/外推，
  相位 0。
- `center`（ITU-R BT.601 / JPEG / H.264 center）：相位 1/2 个亮度采样；
  下采样是 2 抽头平均，上采样是配对的线性插值，因此**线性 ramp 往返不变**。
- `4:4:4` 不做子采样，相位 (0,0)。

每个像素的 `range_tags.chroma_phase` 记录实际使用的相位，混用布局时一眼可见。
相位不匹配半个采样正是"色度镶边 + 高饱和偏色"的典型成因之一，这个模块让它不可能
悄悄发生：编码/解码两侧读的是同一个相位值。

## 双实现路径

- `matrix`：直接求值有理矩阵表达式。
- `lut`：按输入码预计算每通道整数表（经典 codec 做法），每像素三次整数加法一次舍入。
- 两条路径都是精确的，所以要求**逐字节相同**（`compare_paths` / 自检 A5）。
  `yuvkit.lut` 的表格从 `ColorMatrix` 对象本身构建，常量被修正时不会失步。

独立对照实现（`yuvkit.reference`，仅作 oracle，不产出结果）：

- `ReferenceConverter`：float64，逆矩阵用 Cramer 法则数值求逆（而不是抄公式，
  抄错公式的检查才独立）。
- `convert_f32`：IEEE-754 binary32 逐步舍入，模拟 shader / codec。
- `FixedPointReference`：Q16 定点，一次截断，模拟硬件。

## 验收自检

`python3 convert.py --selftest`（约 30 s，输出 JSON，失败返回非零）：

| 检查 | 内容 |
|---|---|
| A1 roundtrip-tolerance | 边界色样（黑/白/饱和原色/灰阶）+ 10⁵ 随机像素 × 3 标准 × 4 范围组合，`max_err ≤ 2`；另加 limited 立方体穷举最坏情况 |
| A2 mixed-standard | 601/709 混用被检出并给出常量差；单标准请求 `warnings == []` |
| A3 banding | 灰阶 ramp 的级数等于理论双重量化值（256→220 级），单调无反转；匹配范围时往返零误差；4:2:0 平面 ramp 不新增 banding |
| A4 independent-matrix | 与独立矩阵实现逐像素对照：`max_delta ≤ 1`，差异集中在舍入边界 |
| A5 path-consistency | LUT 与矩阵路径在全部组合下逐字节相同 |
| A6 chroma-phase | 4:4:4 / 4:2:2 / 4:2:0 及其 left 变体的 siting 往返在界内 |
| A7 lossless-channels | 解析上界与实测分类一致 |

单测：`python3 -m unittest discover -s tests -t .`（159 个用例，含 `tests/test_legacy.py`
里把三处原始缺陷逐条复现的夹具）。

`tests/test_regression.py` 把每条验收项写成独立用例；其中
`test_ramp_matches_theoretical_double_quantisation` 直接与"理想双重量化"逐步比对，
因此 ramp 上没有**新增** banding 是可证明的，而不只是"看起来还行"。
`Acceptance3.test_grey_ramp_levels` 断言 full→limited 之后恰好 220 级（219 个亮度码
+ 端点），多于这个数说明有重复量化，少于则说明有 stall。

## 模块一览

| 模块 | 职责 |
|---|---|
| `yuvkit.standards` | Kr/Kb、别名、`Standard` |
| `yuvkit.ranges` | full/limited 布局、半值向上舍入、截断 |
| `yuvkit.matrix` | 精确有理矩阵、自洽校验、`RawCode` |
| `yuvkit.lut` | 表驱动路径 |
| `yuvkit.chroma` | siting/phase 与可逆子采样对 |
| `yuvkit.converter` | 单像素 API、`PixelResult`、多实现对照 |
| `yuvkit.planes` | 平面 / 4:2:0 整图往返；`reconstructed_luma_max_err` 与传输层 Y' 分开报告 |
| `yuvkit.lossless` | 无损通道界、分类、截断规则 |
| `yuvkit.trace` | 范围标记与追踪容器 |
| `yuvkit.metrics` | ramp、banding 剖面、往返统计、边界距离 |
| `yuvkit.reference` | 独立 oracle（float64 / binary32 / Q16） |
| `yuvkit.request` | JSON 契约、告警、追踪、双路径一致性 |
| `yuvkit.selftest` | 验收自检 |
