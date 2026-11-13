# 离线 HDR 帧转换

Python 3.9+。运行 `python3 convert.py sample.json output.json --debug`。JSON 承载单帧的 10 位码值，Y 为宽高原尺寸，Cb/Cr 各为向上取半尺寸。使用原始码值便于逐像素对照。输出沿用同名 8 位平面。

输入 metadata 必须包含 `primaries=bt2020`、`transfer=pq`、`matrix=bt2020nc`、`range=limited`、`siting=center|left`。保留 siting，输出前四项分别为 `bt709`、`bt709`、`bt709`、`limited`。拒绝缺失或冲突的声明。

- 输入 Y 使用 (Y−64)/876，Cb/Cr 使用 (C−512)/896。色度坐标以 Y 样本中心为整数，center 在 (2i+0.5,2j+0.5)，left 在 (2i,2j+0.5)。按该坐标双线性插值并夹取边缘。
- 用非恒定亮度的 BT.2020 YCbCr 解矩阵，系数 Kr=0.2627、Kb=0.0593。分别将 RGB′ 夹到 [0,1] 并按 ST 2084 EOTF 得到 nits，峰值为 10000 nits。
- 将线性 RGB 转到 BT.709，矩阵三行为 [1.660491,−0.587641,−0.072850]、[−0.124550,1.132900,−0.008349]、[−0.018151,−0.100579,1.118730]。使用转换后的亮度 L=0.2126R+0.7152G+0.0722B。每通道除以 203+max(0,L)，再夹到 [0,1]，然后用 BT.709 OETF。
- 通过 Kr=0.2126、Kb=0.0722 编码 BT.709 YCbCr。输出 C 在 center 位置用其 2×2 支撑的均值，left 使用左列两样本均值，奇数边界复制最边缘的有效样本。Y 的码值为 16+219Y，C 为 128+224C，四舍五入后夹到 Y [16,235]、C [16,240]。
- 不传种子时关闭抖动。给出种子时使用由平面、样本坐标和种子决定的稳定亚码值抖动，幅度不超过半码。
- `--debug` 时附加 `debug` 字段，包含 `rgb2020_nits`、`rgb709_nits`、`mapped_linear709` 三个按行、列、通道排列的浮点网格。


## 按镜头处理的帧序列

加 `--sequence`。仍使用原两个文件路径参数和可选 `--seed`、`--debug`。输入 JSON 对象有 time_base=[正整数,正整数]、target_siting（center 或 left）和非空 frames。每个 frames 项有 pts（整数）、duration（正整数）、可选 cut（布尔值）和 frame（原始平面对象）。按 pts 严格递增，且上一帧 pts+duration 不可超过下一帧 pts。首帧隐式为镜头起点，每个 cut=true 开新镜头。尺寸可在切点改变，其他位置的尺寸变化需报错。色度位置和输入 range 可逐帧变化。

- 传统单帧 CLI 保持原行为。序列输入同时允许 metadata.range 为 full，使用 Y/1023、(C−512)/1023。其他输入元数据和有效采样契约不变。解码按每帧的输入 siting，编码始终按 target_siting。
- 对每个镜头先计算全部像素转入 BT.709 线性光后的 L，以 floor(L+0.5) 四舍五入到 1 nit，夹在 [0,10000]。每个像素按其帧的 duration 计权。p95 是累计权重首次达到总权重 95% 的最小整数 nit。可以流式聚合不超过 10001 桶的统计。计算白参数 W=max(203,p95/2)，该镜头所有帧用 W 替换单帧曲线的 203，并保留相同的颜色处理顺序。
- 输出有 time_base、frames、shots。每个 frames 项含原 pts、duration、shot_start_pts、frame；后者是转换后的平面对象。shots 项为 {start_pts, pixel_ticks, p95_nits, white_nits}，pixel_ticks 是该镜头所有像素权重之和。时间戳及权重使用整数。调试结构沿用单帧。dither 仍基于平面、输出坐标与种子；相同镜头独立回放时其 frame 输出和镜头统计不变。验证整段输入后发布输出，错误不得留下部分文件或破坏已有输出。

## 实现与测试

`convert.py` 是完整重写的实现，单帧与序列共享同一条线性光管线：

1. 逐帧归一化（limited/full range）后按该帧 siting 双线性上采样色度，边缘夹取，奇数尺寸由支撑枚举自然处理；
2. BT.2020 NCL 解矩阵并逐通道夹取，ST 2084 PQ EOTF 解码到 nits；
3. 线性 BT.2020→BT.709 色域矩阵，在线性光计算 BT.709 亮度与高光；
4. 色调映射 `通道 / (W + max(0,L))`，单帧 W=203，序列 W=`max(203, p95/2)`，再走 BT.709 OETF；
5. 按 target_siting（单帧沿用输入 siting）对色度做 2×2（left 为左列）支撑平均，奇数边界复制有效样本，输出 8 位 limited range 与 BT.709 元数据；
6. 可选 `--seed` 启用由 splitmix64(seed, plane, 输出坐标) 决定的稳定亚码值抖动（幅度 <0.5 码），不传种子即关闭；
7. `--debug` 在单帧输出或每个序列帧上附加 `rgb2020_nits`、`rgb709_nits`、`mapped_linear709` 三个浮点网格；
8. 序列先做整段校验（time_base、严格递增 PTS、禁止重叠、切点外禁止尺寸突变、颜色声明一致且合法），再第一遍流式聚合每镜头 10001 桶整数 nit 直方图得到时长加权 P95 与白参数，第二遍转换；输出经临时文件 `fsync` 后 `os.replace` 原子发布，失败时清理临时文件且不动已有输出。

测试位于 `tests/`：

- `reference.py`：不导入生产代码的独立浮点参考路径（标量逐像素、不同求和顺序），以及离线排序真值的镜头统计；
- `test_convert.py`：`python3 -m unittest tests.test_convert -v`，覆盖灰阶/彩边/负中间色向量在奇数尺寸与两种 siting 下 ≤1 码值对照、加权 P95/白参数排序真值、色度位置变化、固定种子复跑、从硬切点独立回放、短高光静止区稳定性、各类无效时间戳/尺寸/颜色声明、CLI 端到端及失败时已有输出零覆盖；
- `edge_checks.py`：1×1、full range、95% 边界、暗部白参数地板、峰值桶与整数时间戳契约的补充检查；
- `make_demo.py`：生成多镜头变帧率演示输入（产物路径已在 `.gitignore`）。

