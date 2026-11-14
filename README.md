# 网络分区 / 乱序下的 Raft 日志复制仿真

Python 3.9+，仅用标准库。离散事件驱动，所有随机量由 `seed` 决定，**同 seed 结果逐字节可复现**。

- `python3 raft_sim.py scenario.json output.json`：跑单个场景并输出完整轨迹 JSON
- `python3 raft_sim.py --demo`：内置脑裂场景，打印事件级决策轨迹与汇总报告
- `python3 acceptance.py`：50 次随机分区/乱序/丢包注入 + 脑裂专项 + 同索引冲突专项

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `raft_core.py` | Raft 节点状态机：选举/投票、AppendEntries、冲突截断、安全提交、顺序 apply |
| `raft_sim.py` | 离散事件队列、网络模型（分区+延迟+乱序+丢包）、定时器、决策轨迹、CLI |
| `acceptance.py` | 验收：随机注入、确定性脑裂时序、同索引双记录、可追溯性与最终一致性断言 |
| `scenario_demo.json` | CLI 场景示例 |

## 场景格式

```json
{"nodes": 5, "seed": 11, "duration": 80,
 "election_min": 16, "election_max": 28, "heartbeat": 3,
 "network": {"delay": [1, 3], "drop": 0.05, "reorder": 0.3},
 "events": [
   {"t": 30, "kind": "client_write", "to": 0, "value": "a=1", "route_to_leader": true},
   {"t": 45, "kind": "partition", "groups": [[0, 1], [2, 3, 4]]},
   {"t": 58, "kind": "client_write", "to": 2, "value": "b=2"},
   {"t": 70, "kind": "heal"},
   {"t": 78, "kind": "checkpoint"}]}
```

事件类型：`client_write`（`route_to_leader` 可选，模拟客户端代理转发）、
`partition`（分组必须覆盖全部节点）、`heal`、`checkpoint`（运行中做不变量快照）、
`drop`（一次性链路黑洞）。

## 三条核心安全机制如何落地

### 1. 任期与投票约束

- 每个节点持久化 `currentTerm` 与 `votedFor`，**同一任期最多投一票**；
  收到更高任期的任何 RPC 立即降级为 follower 并清空投票。
- 候选人日志必须「足够新」：比较最后一条日志的 `(term, index)` 字典序，
  日志落后者拿不到票。
- 选举需多数派（`N//2+1`）票数，定时器代数（generation）保证旧定时器作废，
  同一任期出现两个 leader 会直接抛 `SafetyViolation`。
- 只有**当前任期的 leader** 接受客户端写；已观测到更高任期的旧主是 follower，
  其写请求在节点侧即被拒绝（输出 `rejected`，条目不进入任何日志）。

### 2. 日志冲突按 (任期, 索引) 截断

- AppendEntries 携带 `prevLogIndex/prevLogTerm` 做一致性检查；
  不匹配时 follower 回传标准 `conflictTerm/conflictIndex`，leader 据此回退
  `nextIndex` 重传，避免逐条试探。
- 同索引出现不同任期条目时，**截断该条及其后全部再追加**；
  `_truncate()` 硬性断言截断点不得越过 `commit_idx`，从代码层面保证
  「已提交条目永不被覆盖」，违例即抛异常。

### 3. 安全提交点与严格日志序应用

- leader 只把 `commit_idx` 推进到「多数派 `matchIndex` 已达、且条目
  `term == 当前任期`」的位置（规避 Figure 8 旧任期直接计数提交问题）；
  当前任期条目提交后，其之前所有条目凭日志匹配性质一并提交。
- `commit_idx` 单调不减；follower 跟随 leader 的 `leaderCommit`，
  但不超过自身日志长度。
- 状态机由 `lastApplied` 驱动，**严格按索引 1,2,3… 顺序 apply**，
  无跳号、无乱序；`applied` 永远等于 `log[:commit_idx]` 的值序列。

## 网络与调度模型

- 消息经网络层：分区不可达直接丢弃；其余按 `drop` 概率丢包、
  `delay` 区间随机延迟、`reorder` 概率叠加额外延迟制造乱序。
- 选举超时在 `[election_min, election_max]` 内随机（取心跳周期 5~10 倍，
  属 Raft 活性参数的标准工程取值）；安全不变量不依赖该比例。
- 每个时序事件先登记 `event_id` 再派发，节点决策通过 `record()` 关联到
  触发它的事件；RPC 还带 `origin` 形成因果链，因此**每条决策可追溯到时序事件**。

## 输出 JSON 关键字段

- `nodes[]`：逐节点 `term/role/voted_for/log/index、term、value/commit_idx/last_applied/applied/state`
- `term_log`：任期变化与每次当选的 leader、时刻
- `commit_trajectory`：每个节点提交点推进轨迹（含来源 `quorum/leader` 与事件 id）
- `rejected`：被拒绝的写（原因、当时任期）
- `proposals`：每条客户端命令的最终命运 `committed / rejected / lost_uncommitted`
- `events` / `decisions`：时序事件流与节点级决策流（可追溯性的原始数据）
- `checkpoints`：运行中各时刻的安全不变量快照

## 验收的不变量口径

运行中（含分区期间）允许两个通信域在同一索引持有不同任期的**未提交尾部**
（Figure 8 正常情形），因此运行时校验的安全口径是：

- 选举安全：同一任期至多一个 leader；
- 已提交前缀保留：任意两节点在 `min(commit_i, commit_j)` 长度内日志逐字节一致；
- 应用序列前缀一致：任意两节点的 `applied` 在共同长度内完全相同；
- 状态机顺序：`applied == log[:commit_idx]` 且无跳号。

恢复连通并完成对账（`reconverge` 注入 marker 触发全量日志同步）后，
进一步要求**所有节点完整日志、应用序列、KV 状态机完全相等**，
且少数派/旧主的未提交写已被截断、被拒绝的写不出现在任何应用序列中。

