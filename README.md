# 分区回填与发布

Python 3.9+，仅用标准库。运行 `python3 backfill.py ROOT build demo.json`。输入为 inputs 字典及 nodes 字典。inputs 键代表日级、带地区的源分区，值为 {value,version}。nodes 的每个键代表派生分区，值为其有序依赖键列表。DAG 中周/滚动窗口及跨时区映射已由清单展开，依赖可指向 inputs、其他 node 或缺失的输入键。缺失输入贡献 0，并有单独指纹。node 值为依赖 value 的整数和。依赖列表顺序不变，多个节点可用同一父节点。应拒绝循环（退出码 2，错误信息包含环路径）。

## 版本指纹

为每个源按规范 JSON 数组 ["source",key,version,value] 计算 SHA-256，缺失依赖用 ["missing",key]，派生节点用 ["node",key,[按依赖列表的子指纹]]。规范 JSON 为 UTF-8、separators=(",",":")、ensure_ascii=False，无结尾换行。输出 values 与 fingerprints 只含派生节点。

源分区 version/value 均未变的原样重传不会改变指纹，因此不触发任何重算；仅 version 变化（即使 value 相同）也会产生新指纹并沿依赖传播。DAG 结构本身另有 graph_signature，依赖列表增删改会使相关计划与暂存失效。

## 回填计划与分层执行

`topological_layers` 按依赖深度分层、层内按键升序，计划完全确定。`executed` 是本次实际计算的派生节点，按拓扑层及键升序；指纹未变的节点复用已发布或验证有效的暂存结果，分别标记为 `reuse-published` / `reuse-staged`。

每个执行分区在输出 `trigger_paths` 中给出从触发根（变化源分区或指纹变化节点）到该分区的全部简单依赖路径；`trigger_roots` 列出本次触发根。重复回填时已产出版本按指纹复用，不重复计算。

## 暂存、中断与恢复

ROOT 目录布局：

- `published.json`：最新发布视图，仅含 values/fingerprints，`inspect` 返回它；未构建为空。
- `published.meta.json`：可追踪版本清单，含每个源的版本与指纹、DAG 结构指纹、分层、执行集合、触发根/路径、各暂存版本指纹。
- `staging/progress.json`：运行进度（计划、已完成层、暂存索引、源/清单新鲜度快照）。
- `staging/<转义分区键>.json`：每个已完成分区的暂存版本（值、指纹、层、触发路径）。

`--crash-after-layer N` 使第 N 个派生层（从 1 开始，含未重算层）处理结束、还未公开新结果时退出 97，保留已完成暂存进度；此时 `inspect` 仍返回中断前的已发布结果。`build --resume` 从暂存恢复：按当前清单重新规划，依赖指纹仍匹配的暂存直接复用，失效暂存重算，最后原子发布一致全图（先写 meta 再 `os.replace` 切换 published）。无暂存进度时 `--resume` 报错退出 1。

## 陈旧与部分失败保护

每层开始前与最终发布前都会重读清单并比对源指纹快照、DAG 结构指纹和整份清单指纹。运行中依赖变化时拒绝发布陈旧结果，退出 98，已发布旧结果保持可读，进度保留以便恢复。发布前还会对全图再做一次独立重算校验。

节点计算失败（演练可用环境变量 `BACKFILL_FAIL_NODE=<分区键>` 注入）退出 99，失败分区不会写入最新版本，已发布视图不变，排除故障后可 `--resume`。层间改写清单的演练钩子为 `BACKFILL_MUTATE_AT_LAYER/..._SOURCE/..._VALUE/..._VERSION`。

## 验收测试

`python3 -m unittest test_acceptance -v`。测试内含不依赖引擎图逻辑的独立全量重建与独立依赖枚举器：

1. 对跨周及滚动窗口边界的 6 次改动（滚动两端、跨周边界、原样重传、仅版本变化、缺失分区补齐、跨时区划分），逐分区比较值/指纹与全量重建，并比较实际执行集合与独立枚举器。
2. 在两个依赖层之间改变输入并注入中断（退出 97），验证旧结果可读、有效暂存复用、恢复后每个发布分区的依赖指纹匹配对应数据版本；另覆盖运行中清单变化拒绝（98）、部分失败不发布（99）、循环拒绝（2）。

`rolling_demo.json` 是覆盖三日滚动窗口、跨周汇总、缺失输入与同日期跨时区（cn/us）分区的演示清单，无外部依赖。

