# 多级流水线背压水线调度模拟

Python 3.9+，标准库。`python3 pipeline.py scenario.json output.json`

scenario.json：

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

## 已实现

- **逐级背压**：下游入队满 → 本级持有数据并记 `blocked_ticks`，信号逐级反弹，默认零丢失。
- **水线协议**：`cap` 为硬上界（`high_water` 可核）；stall 期间下游逐级填满，恢复后按 FIFO 按序追平，水位逐级回落。
- **死锁检测**：整 tick 无任何推进且有剩余工作且无 stall → `deadlock:true`；注入/停顿场景不误报。
- **可选字段**（缺省即原行为）：
  - `reject_policy`: `"block"`（默认，拒绝即阻塞并计量）| `"drop"`（丢弃并计入 `dropped`/`drops_by_stage`）。
  - `priorities`: 按 item id 的优先级表；同 tick（同 epoch）内按优先级接纳，epoch 间绝不交叉（`epoch_ok` 校验）。
- **额外输出**：`drops_by_stage`、`high_water`、`epoch_ok`。

## 复跑

```
python3 selftest.py     # 验收：stall 零丢失/顺序/死锁不误报、并发 2/4/8、drop 计量、优先级、CLI
python3 demo_cli.py     # 走 CLI 跑 README 场景并打印水位曲线
```

现有实现缺陷：队满直接丢（dropped 计数）、无逐级阻塞（上游继续推）、stall 后顺序不保证（FIFO 错序）、无死锁检测。
