# 入湖分层存储的压实选层与水位推进

## 运行方式

```
python3 lsm.py ops.json output.json      # 单次请求（兼容旧 ops 结构）
python3 run_acceptance.py                # 2 组层数 × 多随机种子验收矩阵
python3 demo.py                          # 逐次压实选层与回收统计
python3 -m unittest discover -s tests    # 全部单测（24 个）
```

`ops.json` 兼容旧结构，并支持扩展字段：

```json
{"levels":3,"cap":[2,2,4],"seed":7,"mode":"concurrent",
 "ops":[{"op":"put|del|snap|release|read","k":"k01","v":1,"snap":1}]}
```

无 `ops` 时可用 `{"preset":"compact4|deep5","seed":7,"n_ops":240}` 生成混合负载。

## 缺陷基线（保留于 baseline_buggy.py，仅用于对照）

旧实现存在三个已确认缺陷，新引擎逐一修复：

| 缺陷 | 旧实现 | 新引擎 |
| --- | --- | --- |
| 选层随意 | `randint` 随机选目标层，写放大不可控 | 按层容量压力 + 键范围重叠度确定性选文件组（`lake/planner.py`） |
| 快照版本丢失 | 每层只留最新版本，覆盖掉快照可见版本 | 不可变文件 + 快照 pin，旧版本以 orphan 文件保留到快照释放 |
| 水位回退 | `watermark=min(...)` 导致已删除记录复活 | 水位单调只进不退（`WatermarkRegression` 硬断言），墓碑仅在底层且对所有存活快照生效后才清除 |

## 架构与模型约定

- `lake/model.py`：`Record(seq,ts,write_index,value)`、不可变 `FileMeta`（键范围/seq 范围/逻辑字节/固定文件脚注）、`Config`。
- `lake/manifest.py`：分层文件清单、快照 pin 计数、GC 水位、orphan 文件保留、JSON 快照/恢复与 sha1 摘要。
- `lake/planner.py`：确定性选层。L0 按 FIFO 取最旧文件；Ln 选与下层重叠字节最小的种子文件并做范围固定点扩张；同泵轮内忙文件互斥，冲突计划直接延后而非先抢后中止。
- `lake/engine.py`：写入/刷盘、合并淘汰（按 `(seq, ts, write_index)` 总序取最新）、底层墓碑安全淘汰、原子换装、含 memtable 的点读、统计。
- `lake/scheduler.py`：带种子 RNG 的并发压实调度，每个 pump 记录 `pump_start/plan/no_candidate/commit/abort/pump_end`，并带 `after_op` 对齐点；`replay()` 逐条复算计划与提交报告并断言一致。
- `lake/workload.py`：确定性 put/delete/overwrite/长快照混合负载生成器与串行真值库 `ReferenceDB`。
- `lake/runner.py`：串行/并发/重放三种执行模式与统一摘要。

### 关键正确性不变量

1. 合并淘汰以批次序号 `seq` 为主、时间戳 `ts` 与写入序号为辅，旧值永不覆盖新值。
2. 墓碑在非底层一律保留；仅在底层、且其 `seq <= 最老存活快照 visible_seq`（无快照则无条件）时物理清除，保证删除记录不会复活、快照版本不会丢失。
3. GC 水位 = 无快照时当前最大 seq，有快照时 `max(当前水位, 最老快照 visible_seq-1)`，只进不退；被 pin 的文件在快照释放前不回收。
4. 点读合并 memtable、L0（新 flush 优先）及各深层文件，按总序取最新可见版本；快照读只遍历其 pin 的文件。

## 输出

`output.json` 在旧字段（`levels/watermark/compactions/reads/digest`）基础上增加：
`final_kv`、`reference_kv`、`kv_match`、`space_amplification`、`write_amplification`、
`level_summary`、`stats`、`compaction_log`（逐次选层与回收）、`decisions`（可重放决策日志）、
`replay_digest/replay_matches`。

## 验收结论

`run_acceptance.py` 对 `compact4`（4 层）与 `deep5`（5 层）各跑 3 个随机种子，断言：

- 并发读结果与串行参考逐点一致，终态键值集等于真值库；
- 终态空间放大 ≤ 1.2（实测约 1.03–1.07），并发/串行一致；
- 快照读保留可见版本，水位单调；
- 同配置同种子重跑摘要与 sha1 摘要完全一致，并发决策日志可字节级重放。

