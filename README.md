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

现有实现缺陷：队满直接丢（dropped 计数）、无逐级阻塞（上游继续推）、stall 后顺序不保证（FIFO 错序）、无死锁检测。
