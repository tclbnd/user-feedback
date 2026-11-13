# 音频波形概览索引

Python 3.9+，仅用标准库。原始音频为无头、交错的 signed 16-bit little-endian PCM。

## 命令

- `python3 overview.py ROOT build SOURCE --channels N`
- `python3 overview.py ROOT edit EDL.json [--crash-before-publish]`
- `python3 overview.py ROOT query ranges.json [--version N] [--verify-digest]`

build 校验帧对齐（拒绝非整块字节）、保存源标识并构建多层索引；首个 EDL 版本为 1，
初始时间轴是整段源。复用已建索引；索引页损坏时 query 自动从源重建；源标识不匹配
（size/mtime，或 `--verify-digest` 的 sha256）时拒绝返回，绝不拿旧峰值应付。

## 多层索引（需求 1）

源索引 `source.idx` 与每个 EDL 版本的派生索引 `edl/index-N.idx` 都是 4096 字节分页文件：

- 叶块（level 0）聚合 256 帧；每升高一层，一个父块聚合 32 个子块（fanout=32）。
  因此各层块跨度为 256、8192、262144…… 帧，层级数随长度只按对数增长。
- 每个块对每个声道独立记录 `min / max / 平方和(sumsq)`；帧数不进记录，由块的
  层级几何推导，末尾不足一块的残块按 `frames - block*span` 计帧。
- 记录定长 128 字节（8 声道 × 16 字节，含填充），每页恰好 32 条，记录永不跨页。
- 范围查询自顶向下贪婪组合：能整块命中的最高层直接取索引块，只有被范围两端切开的
  叶块才回到 PCM 读取必要的逐采样边缘，因此保留逐采样精度且不重扫全文件。

语义约定：

- **声道**：1–8，各自独立统计，输出按原始声道顺序。
- **静音**：`min=max=0`、`sumsq=0`，但帧数照常计入 RMS 分母。
- **尾部残块**：每层最后一块可短于该层跨度，按真实覆盖帧数处理。
- **空区间**：`min=max=rms=0`，不读任何源字节。RMS = sqrt(sumsq/帧数)。

## 非破坏性 EDL 与局部更新（需求 2）

EDL 为 `{"segments":[{"start":0,"end":42},{"silence":17},...]}`，源半开区间可重复、
可换序，允许零长段。edit 用新列表替换 EDL 并递增版本，旧版本文件保留，可用
`query --version N` 读取。

- 每个叶块带一个内容指纹（对覆盖它的 EDL piece 序列求 blake2b）。指纹相同即证明
  采样序列逐帧一致，无论它在时间轴上的位置如何，都可直接复用旧叶块记录。
- 父块自底向上标记：仅当其下所有子块都复用时才可证明不变，否则重建。跨越源/静音
  边界的叶块必然重建。edit 只读取重建叶块所需的局部源区间，不重扫全源。
- 读者固定读取一个 `edl-N.json` + `index-N.idx` 版本对；新版本在 EDL 与索引都原子
  落盘并改 `current.txt` 前不可见，因此不会混代。

## 页级 I/O、统计与恢复（需求 3）

- 所有读取走 `pread`，`PagedFile` 统计触及的**不同 4096 字节页数**（`index_pages`）
  与精确字节数（查询返回 `raw_bytes`）。不存在「整文件读后只报部分」。
- 每次 build/edit/query/恢复追加一行 JSON 到 `stats.log`，记录页数、字节、复用/重建
  块数、耗时；edit 返回 `leaves_reused / leaves_rebuilt`。
- 每页 CRC-32 存入头部校验表，头部自身带自校验 CRC。损坏页在首次读取时被检测：
  派生索引损坏 → 由源 + 该版 EDL 重建；源索引损坏 → 从源 PCM 重建。
- 所有写操作走 `*.tmp`/`*.stage` + `fsync` + `os.replace`，崩溃只留下临时文件，
  读者永远只能看到完整版本。`--crash-before-publish` 以退出码 97 模拟发布前崩溃。
- `meta.json` 记录源路径、大小、mtime、sha256、声道数、帧数；源被替换或触碰导致
  标识不符时 query 直接报错（退出码 2），拒绝使用任何索引结果。

## 验收测试（`tests/`）

- `python3 tests/test_correctness.py`：固定种子，40 次剪辑 × 每轮 1002 个范围
  （含每层块边界与残块偏移），与独立实现 `tests/reference.py`（不共享任何索引代码、
  按 EDL 逐帧展开）逐声道比对 min/max/RMS，共约 37 万项标量检查。
- `python3 tests/test_scaling.py`：长度相差 10 倍的两组素材，64 个等宽概览段，
  断言建索引后查询读取量次线性（raw_bytes 不随长度增长）；并做页损坏重建、源标识
  不匹配拒绝、发布前崩溃与半更新注入。
- `python3 tests/test_reuse.py`：前缀/换序/纯静音剪辑的块复用与零源 I/O 不变量。
- `python3 tests/test_semantics.py`：1–8 声道、极端 int16、残块、空时间轴、帧对齐。
- `python3 tests/run_all.py`：依次运行以上全部套件。

