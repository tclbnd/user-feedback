# 多副本日志复制模拟（任期约束 + 安全提交点）

Python 3.9+，标准库。`python3 raft_sim.py scenario.json output.json`

模拟 N 节点集群的确定性调度。scenario.json：

```json
{"nodes":3,"seed":7,
 "events":[
  {"t":0,"kind":"client_write","to":0,"value":"a"},
  {"t":1,"kind":"partition","groups":[[0],[1,2]]},
  {"t":2,"kind":"client_write","to":1,"value":"b"},
  {"t":3,"kind":"heal"},
  {"t":4,"kind":"client_write","to":0,"value":"c"}]}
```

节点状态：`{id, term, role("follower"|"leader"), voted_for, log:[{term,value}], commit_idx, applied:[]}`。

规则（Raft 语义精简版）：

- **选举**：某节点收到 `client_write` 而自己非 leader 时触发选举（term+1、自荐）；同一 `groups` 分区内得票过半（>N/2）者成 leader，term 记录于 `term_log`。分区中不足多数者无法成为 leader，写请求记入 `rejected`。
- **复制**：leader 收到 `client_write` 后，日志追加 `{term,value}` 并尝试复制给同分区其他节点；收到者追加（若同 index 存在不同 term 的记录则**截断**该条及之后全部再追加）。
- **提交点**：leader 的 `commit_idx` 只能推进到「已被多数派持有且该条 term == leader 当前 term」的最后一个 index；follower 的 commit_idx 跟随 leader（按复制时携带的值，不超过自己日志长度）。`applied` = log[:commit_idx+1]（含 index0）。
- 过期 term 的 leader 在 heal 后仍收到写时：若另一节点已以更高 term 当选，旧主写被拒绝（记入 `rejected`，value 不进入任何日志）。
- 分区组的 `groups` 划分通信域；`heal` 恢复全联通。调度按 `t` 升序、同 t 按列表序，**必须确定性**（不用真随机，seed 仅用于节点选择 tie-break）。

输出：

```json
{"nodes":[{"id","term","role","log","commit_idx","applied"}...],
 "rejected":[{"t","to","value","reason"}],
 "term_log":[{"node","term","elected_at"}]}
```

现有实现缺陷：不做多数派检查（任何分区都能当选）、commit_idx 直接推进到日志末尾（不含 term 检查）、旧主写不被拒绝、截断逻辑缺失导致同 index 双记录共存。
