# 多级流水线背压水线调度模拟

Python 3.9+，仅标准库。`python3 pipeline.py scenario.json output.json`

```json
{"stages":[{"name":"src","cap":8},{"name":"mid","cap":4},{"name":"snk","cap":4}],
 "items":40,"stall":{"stage":"snk","from":10,"to":20},"concurrency":1,
 "seed":1}
```

- 每 tick：各 stage 从其入队（上一 stage 出队或注入源）按序取 1 条处理推入下一队；`cap` 为该 stage 入队缓冲上限。
- **背压**：入队满 → 上游阻塞（`blocked_ticks` 累加），不得丢数据；stall 期间 `snk` 不消费，上游逐级反弹填满。
- 恢复后按序追平：同一代次（同上游 epoch）数据不得交叉乱序；stall 结束后 `snk` 恢复消费，水位逐级回落。
- 死锁检测：若某 tick 全员阻塞且无 stall → `deadlock:true`（本场景不得误报）。
- `concurrency`：src 每 tick 注入条数；并发变化不得改变级间顺序与吞吐语义。

输出 `{"dropped":0,"deadlock":false,"water_levels":{stage:[逐tick水位]}, "blocked_ticks":{stage:n},"throughput":n,"order_ok":true,"ticks":n}`。items 全部进入 snk 或仍在缓冲即结束（上限 10000 tick）。

## 实现

### 1. 逐级背压与水线协议

每个 stage 一个 `BoundedBuffer`（FIFO），**先问下游、再出队**：只有下游 `offer` 返回 `accepted`/`dropped`
时才从本级出队；返回 `blocked` 时条目留在原地，本级计一次 `blocked_ticks`（这就是可重试通道）。
因此队满只阻塞、不丢数据。

水位线带滞回（`low`/`high`，默认 `high=cap`、`low=high-1`）：

| 状态 | 行为 |
| --- | --- |
| `len < high` | 正常接纳 |
| `len >= high` | 断言压力，上游停止；若 `high < cap`，`cap-high` 的余量只留给优先级流量 |
| `len <= low` | 解除压力 |

压力信号是锁存的，避免在单条水位线附近抖动时上下游反复启停。

按优先级接纳由 `priority_threshold` 开启：压力状态下只有 `priority >= threshold` 的条目能用预留余量。
条目优先级来自 `priorities`（与 items 等长的列表或一个默认值），或 `items` 用对象数组时的 `priority` 字段。

### 2. 拒绝策略（可配置、逐级计量）

`reject_policy`（可写在全局，也可写在单个 stage 上，后者优先）：

| 值 | 语义 | 计数字段 |
| --- | --- | --- |
| `block`（默认） | 永不丢弃，阻塞上游 | `blocked_attempts` / `blocked_ticks` |
| `drop_newest` | 拒绝新到条目 | `rejected` |
| `drop_oldest` | 挤掉队头条目 | `evicted` |
| `priority_preempt` | 挤掉优先级最低的条目，等价时拒绝新条目 | `evicted` / `rejected` |

`dropped` = 各级 `rejected + evicted` 之和；每级 `dropped` 也单独输出，便于定位。

### 3. 恢复顺序与代次

`stall` 支持单个对象或数组（`stalls`），可带 `from`/`to` 或 `from`/`duration`。

- 停顿期间：被停的 stage 不消费，其入队缓冲填满 → 上游阻塞 → 再上一级填满，逐级反弹。
- 恢复时：调度按 stage 下标顺序推进，下游先腾出槽位，上游随后才可能交出条目，
  因此恢复自下游向上游依次发生（`metrics.recovery.sequence` 记录实测顺序）。
- 代次：停顿开启与结束时各推进一次 `generation`。所有队列严格 FIFO 且条目只有被下游接纳才出队，
  所以后一代（`generation` 更大）的条目不可能越过前一代，`order_ok` 同时校验序号单调与代次单调。
- 旁路：某个 stage 停顿不会中断本轮扫描，其下游队列继续排空；压力只沿上游传播。
  因此慢/停消费者只影响自己和它的生产者，不影响其他队列。

### 4. 死锁检测

不再用"某 tick 无进展"直接判定。无进展时运行一次**无副作用探针**：若有 stage 能把手头条目
交给下游、或被停的 stage 只是等待一个已声明的可恢复停顿、或源还能注入，就还有进展可能。
声明 `stall` 期间因此绝不误报；而真正不可推进的拓扑（如 `cap: 0` 的缓冲）即使落在 stall
窗口内也会报 `deadlock: true`，并给出 `metrics.deadlock_tick`。

### 5. 指标

顶层输出保持文档契约不变。`metrics`（`run()` 里用 `"include_metrics": true` 打开，
或直接调用 `simulate()`）逐级给出：`cap/high/low/policy/level/peak/pressure/accepted/dropped/
rejected/evicted/blocked_attempts`、`blocked_ticks`、`stalled_ticks`、`max_level`、`mean_level`、
`first_drain_after_stall`，以及源端 `injected/pending/blocked_ticks`、`generation_events`、
`recovery` 与 `deadlock_tick`。

## 原实现的缺陷与对应修复

| 原缺陷 | 现实现 |
| --- | --- |
| 队满直接丢（`dropped` 计数） | 队满只返回 `blocked`，条目留在生产者队头（可重试通道）；`blocked` 策略下 `dropped` 恒为 0 |
| 无逐级阻塞（上游继续推） | 先向下游 `offer`、成功后才 `popleft`，压力逐级向上反弹 |
| stall 后顺序不保证（FIFO 错序） | 全程严格 FIFO + 出队前确认下游接纳，`order_ok` 同时校验序号与代次单调 |
| 无死锁检测 | 无进展时做无副作用探针，区分"等待已声明停顿"与"真卡死" |

## 验收对照

`PYTHONDONTWRITEBYTECODE=1 python3 tests/test_pipeline.py`（161 项断言）

1. **下游停顿时零丢失、按序追平、死锁不误报**：`test_no_loss_and_order`、`test_recovery_order`、
   `test_recovery_order_mid_pipeline`、`test_deadlock_detection`、`test_slow_consumer_is_bypassed`。
   停顿注入下 `dropped==0`、完成序列为 `0..n-1`、`deadlock==false`，恢复顺序为 `snk → mid → src`；
   另含"无限期停顿不误报死锁"与"零容量缓冲确实报死锁"两个反向用例。
2. **2/4/8 并发一致、占用不超上界**：`test_concurrency_equivalence`、
   `test_reference_equivalence`、`test_bounds_never_exceeded`。并发不改变 tick 数、吞吐、
   完成顺序；所有 `water_levels` 与 `peak` 均 `<= cap`。
3. **水线/优先级/拒绝策略**：`test_waterline_hysteresis_and_headroom`、
   `test_priority_admission`、`test_rejection_policies`。
4. **配置边界**：`test_configuration_edges`（空拓扑、0 items、重名 stage、非法数值、
   未知停顿目标、`duration` 形式）。
5. **一个屏幕看结论**：`PYTHONDONTWRITEBYTECODE=1 python3 accept.py`（任一断言失败即非零退出）。


