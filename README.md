# 公平份额抢占调度与记账一致

Python 3.9+ 标准库，无第三方依赖。

```
python3 fair.py sim.json output.json            # 调度
python3 fair.py events.json bills.json --replay  # 只重放事件流重新计费
python3 fair.py sim.json out.json --chunk 3      # 分块回放，结果不变
python3 selftest.py                              # 21 项不变量/验收测试
```

## 输入
```json
{"jobs":[{"id":"a","w":1,"cost":10,"arrive":0,"rate":1}],
 "horizon":60,"quantum":1}
```
- `w` 权重（>0），决定份额 `w/Σw`
- `cost` 工作量（秒），`arrive` 到达时刻，`rate` 单价（默认 1）
- 也接受 `chunks:[{"jobs":[...]}, ...]` 的分块到达流，决策与平铺输入完全一致

## 输出
```json
{"events":[{"t":0,"job":"a","kind":"start"}, ...],
 "bills":{"a":10},                 // 逐作业有效秒数（=Σ 实际运行段）
 "preempts":{"a":1},               // 被抢占次数（不含公平轮转的让出）
 "finish_order":["a"],
 "util":0.17,                      // Σbusy/horizon
 "decisions":[{...}],              // 每次调度决策：模式/份额/权重/触发原因
 "share_util":{"a":1.0},           // 实际份额 = 有效时间/忙时
 "details":{"a":{...}}}            // 逐作业账单：cost/checkpoint/effective/bill/份额
```

## 关键行为
- **按份额抢占**：高权重作业到达时暂停低权重运行者。暂停是**检查点**而非 kill：
  已服务时间保留，恢复后从剩余工作量续跑。
- **记账分段**：`start / pause / resume / finish` 事件流。账单 = Σ(各运行段长度)，
  永远不会把挂起墙钟时间算进去。没有检查点的旧实现会把 `cost*rate` 翻倍。
- **重放一致**：`replay()` 只吃事件流，每作业至多一个打开段，重复/回滚的
  `finish` 被忽略，因此重放账单与调度账单逐位相同。
- **公平轮转不误记**：`pause` 既可能来自抢占，也可能来自 WFQ 轮转；`preempts`
  只统计前者，`decisions[].reason` 给出 `preempted` / `starved`。
- **防饿死**：任一就绪作业等待达 `STARVATION_BOUND`(20s) 立即被强制运行一个
  quantum，然后交还 WFQ；高权重突发下低权重作业饥饿时间有上界。
- **防抖动**：同一作业被抢占超过 `PREEMPT_LIMIT`(3) 次后进入 `IMMUNITY_SECONDS`(20s)
  免疫窗口，期间新到达的重作业不能再次抢占它。
- **分块无关**：`--chunk`/`simulate_chunked` 只改变批处理粒度，不改变任何决策；
  `replay_chunked` 同理。
- **份额精度**：连续虚拟时间 (WFQ) + 按权重闭式切分，3 种权重配置的份额误差远小于
  5%（长程可到机器精度）。

## 基线缺陷（已修复）
原实现抢占时直接 kill 且 `bills[run]=t-start` 记满墙钟时长，重跑再记一次 ⇒ 计费翻倍；
且无事件流、`util` 恒为 0、无防饿死/防抖动。

