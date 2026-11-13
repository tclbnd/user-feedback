# APNG 帧查看器

Python 3.9+，使用标准库。先运行 `python3 make_demo.py`，再执行 `python3 view.py demo.png output.json`。支持非隔行、8 位 RGBA PNG/APNG。保留现有 PNG 滤波器读入路径。动画可将默认图像作为第一帧，也可让默认图像与动画序列分离。验证 acTL 数量与顺序、fcTL 和 fdAT 的连续序号、矩形范围、数据块完整性与 CRC。无效输入退出 2，stderr 对三类用例包含 rectangle、sequence、truncated。

可选 `--request request.json`，文件含 frames（整数帧索引数组）。不提供时遍历全部帧。返回 frames 列表，顺序与请求一致。每项为 index、start、duration、rgba_sha256，其中 start 和 duration 是约分后的 [分子,分母] 秒。den=0 视为 100。SHA-256 对整幅显示画布按行的 RGBA8 字节计算。OVER 使用非预乘 RGBA 和源覆盖的精确公式，每次像素合成后四舍五入到最近整数，半整数向上。完全透明的 OVER 源像素保留目的像素。SOURCE 原样覆盖，BACKGROUND 清成 (0,0,0,0)。每次循环从透明初始画布开始。单帧 PNG 仍可读取，时长定义为 [0,1]。

`--budget 4` 最多保留 4 张持久的处置后检查点。透明初始态可隐式重建，临时显示态与恢复前画布不计入持久检查点；不得持久保存所有显示帧。stats 包含 max_checkpoints 和 max_replay_frames，后者是索引建立后单次请求重放的最大帧数（包含请求帧）。120 帧、预算 4 时该值 ≤ 32。使用本地小图复跑，画布哈希应一致。
