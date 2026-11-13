# 复合键外存连接（含热点键倾斜处理）

Python 3.9+ 标准库实现，无第三方依赖。解决“单个键占据近半输入、按哈希
分区无法继续拆小”的倾斜连接问题，同时严格保留 SQL 内连接与左外连接语义。

## 命令

```
python3 join.py left.csv right.csv result --type left --memory-mib 64
python3 join.py left.csv right.csv result --type inner --max-open-fds 8
python3 join.py left.csv right.csv result --crash-before-publish
```

CSV 为 UTF-8 标准带引号格式，表头固定 `key_a,key_b,value`。键中的空字符串
视为 SQL NULL，其余值逐字匹配，值可包含逗号、引号、换行（由 csv 模块正确
转义）。两侧均可有完全重复的行。输出 `rows.csv` 表头为
`key_a,key_b,left_value,right_value`，左外连接的未匹配行 `right_value` 为空。

## 连接语义

- **复合键**：`(key_a, key_b)` 同时相等才匹配，使用稳定 BLAKE2b 哈希分区，
  不依赖带随机种子的内建 `hash()`，重跑结果可复现。
- **NULL**：任一键分量为空即 SQL NULL；`NULL = NULL` 不为真，因此含空键的
  行永不匹配（含两侧都为 NULL 的情况）。左外连接中左侧 NULL 行作为未匹配
  行原样输出，右侧 NULL 行在任何连接类型下都不产生输出。
- **重复行 / 多对多**：保留全部重复，按笛卡尔积展开，逐条流式写出，绝不把
  结果集累积在内存。
- **空输入 / 单侧空**：内连接产出 0 行；左外在右侧为空时输出全部左侧行
  （含未匹配标记）。

## 执行路径（按工作内存预算自动选择）

1. **第一遍粗分区**：固定 4 个粗分区，刻意不按预算切碎，使单个热点键的全部
   行集中落入同一个超大分区；空键分量进入独立 NULL 桶。
2. **逐分区决策**：
   - 估计可整装入预算 → **常规内存哈希连接**（构建侧哈希表 + 探测侧流式）；
     左外连接固定以右侧为构建侧以识别未匹配。
   - 否则进入外存路径，做**两遍有界热点检测**：先只统计较小一侧的不同键，
     再流式扫描较大一侧、仅对候选键计数（大侧的全部不同键不进内存）。
   - 识别出多对多放大的热点键后，将其两侧各自**剥离成独立分段文件**，对每个
     热点键做**分段块连接**：较小侧按预算切块、每块驻留时把另一侧完整流式
     扫描，笛卡尔匹配逐行输出；两侧都可以远大于内存。
   - 残余行递归走相同决策；无倾斜的高基数分区在递归层 16 路再分区细分；
     达到递归深度上限或无法识别倾斜时，退化为**有界块嵌套循环（BNL）**兜底，
     块在建表越界时自动对半拆分，严格不突破内存上界。

## 资源约束与计量

- 工作内存预算 `--memory-mib` 是**硬上界**：`MemoryBudget.charge` 采用预判式
  计费，任何托管结构（哈希表、计数表、分块、分段列表）在越过可用预算（预算
  的 85%，预留 15% 给解析瞬时对象）前即抛出并切换外存路径。`_SUCCESS` 中的
  `peak_managed_bytes` 为托管峰值，`peak_rss_bytes` 为进程真实驻留峰值。
- **临时文件句柄数有硬上限**（`--max-open-fds`，默认 16）：读、写句柄分别
  LRU 管理、共享总预算，打开新句柄前强制回收最久未用句柄；
  `peak_open_fds` 记录实际峰值。
- 计量 `spill_bytes`（溢写字节）与 `spill_read_bytes`（回读字节，含多次扫描）。

## 输出、完成标记与故障恢复

- 结果先写入目标目录**之外**的暂存目录，全部完成后才原子 `os.replace` 发布；
  最终目录只含 `rows.csv` 与 `_SUCCESS`。
- 目标目录已存在时拒绝覆盖（退出码 2）；任何 I/O 异常或 SIGTERM/SIGINT 都会
  回收本次暂存且绝不发布半成品。
- `--crash-before-publish` 在发布前无清理退出 97，目标目录不创建；无故障
  重跑会自动清理“属于该目标”的上次暂存并安全重新发布，不触碰其他目录。

## 稳定可核对的输出

输出按**多重集**而非顺序核对。`_SUCCESS` 含 `multiset_rows` 与
`multiset_digest`：对每行 4 列字符串的紧凑 JSON 取 BLAKE2b-256，按 256 位
整数取模累加（加法满足交换/结合律，故与输出顺序无关）。第三方仅凭
`rows.csv` 即可用 `verify/recompute_digest.py` 独立复算。若需要逐行顺序核对，
约定稳定排序键为 `(key_a,key_b,left_value,right_value)`（空键为空串）。

## 验收脚本

```
python3 verify/make_small_fixture.py     && python3 verify/run_small_acceptance.py
python3 verify/make_multihot_fixture.py  # 双侧大热点
python3 verify/make_uniform_fixture.py   # 无倾斜高基数
python3 verify/run_all.py                # 上述全部 + SQLite 精确核对
python3 verify/make_large_fixture.py     # s=1..8 热点体积翻倍夹具
python3 verify/run_cases.py '[{"scale":8,"type":"inner"}]'
python3 verify/large_check.py            # 闭式计数/内存有界/溢写线性
```

- 小型、多热点、无倾斜夹具均以 **SQLite 内存库**重算内/左外连接，把结果归约
  成“元组 → 出现次数”的字典做精确多重集比对（NULL 用普通 `=` 而非空值安全的
  `IS`，正确复现 NULL 不匹配）。
- 大型夹具用**闭式键计数**逐桶核对输出（如热点键输出 = 左行数 × 右行数），
  并断言数据翻 8 倍时托管内存峰值基本持平、溢写量线性增长。
