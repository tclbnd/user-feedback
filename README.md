# 双时间维表关联（业务时间 × 系统时间）

Python 3.9+，仅用标准库。解决的问题：统计每笔事实时既要匹配**当时生效**的维表版本，
也要能**复现过去出报表时已知**的版本；补录/回溯修订会改变今天重算的结果，但历史报表
仍然可解释、可重放。

## 双时间模型

- **业务时间（valid time）**：维表断言 `[from,to)`、事实时刻 `at`，半开区间，`to=null` 开放。
- **系统时间（system/transaction time）**：事件的 `system` 获知时间。报表 `asof` 含该时刻事件。
- 维表内部存成双时间单元格 `DimCell`：业务区间 × 系统区间，两个区间都用半开 `[lo,hi)`。
- 事实存成系统时间版本链 `FactVersion`；关联结果存成带版本来源的 `Link` 系统时间链。

### 三种修订

- **拆分**：`upsert` 只覆盖命中的业务区间，旧断言在修订范围外保留为左右残余单元格。
- **回收（遮蔽）**：`remove` 在区间内写入 `removed` 墓碑，关联结果 `reason=removed`。
- **撤销**：更晚 `system` 的 `upsert` 重新覆盖墓碑区间；旧报表仍保留当时的 `removed` 结论。
- 事实 `op=delete` 撤销事实本身：当前报表不再输出，但事实版本与历史关联链完整保留。

### 冲突与未匹配

- 同一 `system` 刻度对同一键的不同 `version`，按 `version` 字典序较大者胜出（同刻度替换，不留零长区间）。
- 同 `version` 同载荷是重复事件（批内/跨批都幂等，无影响）；同 `version` 不同载荷整批拒绝，退出码 2，状态文件不变。
- 未匹配分两类原因：区间缺口 → `reason=no_dimension`；被墓碑遮蔽 → `reason=removed`；正常 → `reason=matched` 并带 `dimension_version/label`。

## 增量重算与稳定顺序

- 事件按 `(system, version)` 全局稳定排序，乱序到达、不同批次切分、跨分区（事件可选带 `partition`）都得到同一结果。
- 维表修订只重算“同实体且业务时刻落在修订区间内”的当前有效事实；事实事件只重算该事实自身。`apply` 返回 `affected`（实际重关联键集合，升序）。
- 关联链用 **coalesce**：视图（reason + 维表版本）未变化就不切新系统时间段，因此段边界与批次粒度无关；每次重算仍逐条写入变更明细 `changes`（含 `old/new/changed/coalesced`）。
- 一次 `apply` 原子发布（临时文件 + rename），失败不会留下半成品状态。

## 命令

```bash
python3 bitemporal.py apply state.db demo.json
python3 bitemporal.py query state.db --asof 9                      # 系统时间报表
python3 bitemporal.py query state.db --asof 100 --entity R --valid-from 10 --valid-to 30
python3 bitemporal.py dim   state.db --entity R --at 20 --asof 30  # 维表双时间单元格
python3 bitemporal.py fact  state.db --id F2                       # 事实版本+关联历史
python3 bitemporal.py changes state.db --since 20 --until 60       # 变更明细
python3 bitemporal.py rebuild state.db --asof 9                    # 独立全量重建后出报表
```

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `bitemporal.py` | 双时间引擎：维表单元格、事实版本链、关联链、增量重算、原子持久化、CLI |
| `reference_rebuild.py` | **独立**全量重建（另一套区间折叠算法，不共享引擎代码路径），作为审计基准 |
| `scenario.py` | 合成数据：初始装载 + 8 次回溯修订、12 个切点、手工影响范围与预言机期望 |
| `test_acceptance.py` | 23 个 unittest 验收/语义测试 |
| `property_check.py` | 300 个随机差分序列，逐切点比对引擎/流式/乱序与全量重建 |
| `run_demo.py` | 端到端证据打印 |

## 验收对应关系

1. **8 次回溯修订 × 12 个切点**：`test_01` 对每条事实比对增量引擎、独立全量重建与手工预言机的维表版本；`test_02` 检查 4 对相邻系统时间区间边界点；`test_03` 检查 6 对业务时间半开边界。
2. **回放与影响范围**：`test_04` 把每次修订实际重关联的键集合与手工计算核对；`test_05` 用批次大小 1/2/3/5 与乱序回放同一组版本，验证当前结果和旧报表一致、双时间结构逐字节相同；`test_06` 重复一条修订验证幂等；`property_check.py` 再做 300 组随机差分。

```bash
python3 test_acceptance.py -v   # 23 个测试
python3 property_check.py       # 随机差分
python3 run_demo.py             # 打印完整验收证据
```

