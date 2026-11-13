# APNG 帧查看器

Python 3.9+，使用标准库。先运行 `python3 make_demo.py`，再执行 `python3 view.py demo.png output.json`。支持非隔行、8 位 RGBA PNG/APNG。保留现有 PNG 滤波器读入路径。动画可将默认图像作为第一帧，也可让默认图像与动画序列分离。验证 acTL 数量与顺序、fcTL 和 fdAT 的连续序号、矩形范围、数据块完整性与 CRC。无效输入退出 2，stderr 对三类用例包含 rectangle、sequence、truncated。

可选 `--request request.json`，文件含 frames（整数帧索引数组）。不提供时遍历全部帧。返回 frames 列表，顺序与请求一致。每项为 index、start、duration、rgba_sha256，其中 start 和 duration 是约分后的 [分子,分母] 秒。den=0 视为 100。SHA-256 对整幅显示画布按行的 RGBA8 字节计算。OVER 使用非预乘 RGBA 和源覆盖的精确公式，每次像素合成后四舍五入到最近整数，半整数向上。完全透明的 OVER 源像素保留目的像素。SOURCE 原样覆盖，BACKGROUND 清成 (0,0,0,0)。每次循环从透明初始画布开始。单帧 PNG 仍可读取，时长定义为 [0,1]。

`--budget 4` 最多保留 4 张持久的处置后检查点。透明初始态可隐式重建，临时显示态与恢复前画布不计入持久检查点；不得持久保存所有显示帧。stats 包含 max_checkpoints 和 max_replay_frames，后者是索引建立后单次请求重放的最大帧数（包含请求帧）。120 帧、预算 4 时该值 ≤ 32。使用本地小图复跑，画布哈希应一致。

## 解码与合成结构

实现把三份状态严格分开：

* **帧解码**：`parse()` 只保留压缩帧描述（fcTL 参数加上 IDAT/fdAT 后的 zlib 数据），不生成画布。
* **显示态**：`replay_display()` 得到的整幅显示画布，提交后立即哈希，随后丢弃。
* **处置后画布**：`build_checkpoints()` 中每帧处理完后留下的画布，正是下一帧的起点。

每帧只在 fcTL 指定的局部矩形内合成，矩形外保持原样（因此局部更新区以外为透明）。处置按 `dispose_op` 执行：NONE 保留显示态，BACKGROUND 把该矩形清成 (0,0,0,0)，PREVIOUS 回填绘制前保存的矩形。首帧的 PREVIOUS 按 BACKGROUND 处理。

## 随机访问与检查点

`checkpoint_indices()` 在 `[0, 帧数-2]` 上均匀取至多 `--budget` 个索引（末帧之后的状态没有用处，不占预算），`build_checkpoints()` 顺序扫描一遍只留下这些处置后画布。任意请求从"小于请求帧的最近检查点"（没有则用透明初始画布）开始，只重放必要区间。

因此跳转、循环播放与方向反转都不复用上一次请求的中间状态：显示画面与逐帧播放完全一致，带 PREVIOUS 处置的帧不会污染后续画面；帧起始时刻只由 fcTL 延时前缀和决定，与访问顺序无关。单次请求重放帧数不超过区间长度，120 帧、预算 4 时为 24（≤ 32）。

画布驻留量单独计量（`view.canvas_stats()`，不进入输出 JSON）：同时存活的全尺寸画布不超过检查点数加一张工作画布与一块临时矩形，测试据此断言预算约束。

## 测试

`python3 -m unittest discover -s tests -t .`

`tests/fixtures.py` 按 APNG 规范独立实现顺序合成基准（逐像素元组、逐帧处置），并提供确定性夹具：120 帧、同一像素连续经历 PREVIOUS/BACKGROUND、延时约分与透明源混合均有覆盖。测试覆盖逐帧对照基准、固定种子 400 次乱序跳转、循环与反向播放、预算为 4 时的检查点/重放/驻留统计、画布哈希与访问顺序无关，以及越界矩形、重复帧序号、截断数据的退出码 2 与错误信息。

