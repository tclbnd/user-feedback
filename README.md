# 乱序时序分层缓冲与窗口压实

Python 3.9+，仅标准库（heapq/dataclasses/hashlib）。

- 引擎：`python3 tsdb.py request.json output.json`
- 验收（三写入顺序 + 逐时刻离线真值比对）：`python3 verify.py`，报告同时写入 `verify_report.txt`

request.json：

```json
{"watermark":30,"levels":[64,256],
 "points":[{"ts":100,"key":"a","v":1,"ver":1},...],
 "queries":[{"at":150,"key":"a"},{"at":200}]}
```

## 分层架构（LSM 风格）

```
写入 ── 水位判定 max_ts - watermark
        ├─ ts >= 水位（窗口内）→ L0 内存缓冲（到达序，容量 levels[0]）
        │     L0 超容 → 整体排序去重，密封为不可变有序 run → L1（容量 levels[1]）
        │     L1 超容 → k 路归并压实 → L2（无界，可多次大压实）
        └─ ts <  水位（迟到超水位）→ 补偿通道 late_buf/late_runs（独立密封与统计，永不回灌常规层）
查询 ── L0 + L1 各 run + L2 各 run 做 heapq k 路归并，跨层合并去重后按时间序输出
```

语义：

- `points` 按写入序到达（时间戳乱序）；运行水位 = 已见最大 ts − `watermark`，早于水位的点为「迟到超水位」，只进补偿通道 `late`，不参与常规查询（`scan(..., include_late=False)`）。
- 每个密封 run 按 `(key, ts)` 有序；压实归并时同 `(key, ts)` 取 `(ver, 到达序 seq)` 最大者——`ver` 大者胜，等 `ver` 后到者胜，重复点计入 `dup_removed`，从无重复键、无旧版本覆盖新版本。
- `queries`：`{"at":T,"key":k}` 查 key 在 `[0,T]` 内所有点，按 ts 升、同 ts 按 ver 降（再按 key 升保证确定序）；无 key 查全部 key。结果与离线全排序逐点一致。
- `finalize()` 做确定性终态大压实：无论中途 run 如何划分，定型后各层布局只由点多重集决定。

## 输出

```json
{"level_stats":{"L0":n,"L1":n,"L2":n,"late":n,"compactions":n},
 "results":[{"at":T,"key":k,"points":[{"ts","key","v","ver"}...]}...],
 "digest":"sha256前16(规范化常规层/补偿通道内容)",
 "finalized":{...定型后逐层快照...},
 "digest_final":"与 digest 相同（压实只改布局不改内容）",
 "compaction_detail":{"L0->L1":n,"L1->L2":n,"L2_major":n,
                      "late_seal":n,"late_major":n,
                      "dup_removed":n,"late_dup_removed":n,
                      "in_regular":n,"in_late":n},
 "late_points":[...补偿通道获胜点...]}
```

## 一致性证明（详见 verify_report.txt 的 I1–I3）

- **I1 不可变性**：run 一旦密封只被归并、从不原地改写；迟到点只进补偿通道，杜绝回灌污染。
- **I2 归并结合律**：`merge_runs` 是对 `(key,ts)` 分组取 `max(ver,seq)` 的结合归约，结果与 run 划分、压实时机无关 → 无重复、无覆盖错误。
- **I3 真值等价**：每个检查点的 `scan`/`get` 与独立朴素离线模型逐点比对，全通道终态合并等于离线全排序；三种写入顺序（原序/洗牌/时间倒序）的摘要与各层点数完全相同 → 写入顺序变化不影响查询结果。

## 对旧实现三类缺陷的修复

旧 `tsdb.py`：不分层全堆一层（无水位判断，迟到点混入正常查询）、假压实不去重（同键多版本并存）、查询按写入序排序。新实现分别由「水位分层 + 补偿通道」「有序 run + 版本决胜归并」「k 路归并时间序查询」修复，并由 `verify.py` 回归守护。

