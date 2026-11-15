# 对账快照的读己之写与长事务下版本回收

`python3 mvcc.py input.json output.json`

input.json: `{"ops":[{"tx":1,"op":"begin"},{"tx":1,"op":"write","k":"a","v":1},{"tx":2,"op":"begin"},{"tx":2,"op":"read","k":"a"},{"tx":1,"op":"commit"},{"op":"gc"},{"tx":1,"op":"end"}...]}`

模型约定：
- begin 分配递增 ts;read 先查本事务 writes 再按快照可见性读(最新 commit_ts ≤ begin_ts)
- 未提交事务的写入对其他事务不可见
- gc 只回收对所有活跃快照不可见的旧版本(水位=最老活跃 begin_ts),不得回收快照仍可见的版本
- 输出 `{"reads":[{tx,k,v}],"versions":{k:[[ts,v]]},"gc_stats":{"reclaimed":n},"digest":"sha1"}`
- 事务交错顺序复跑终态与统计一致

缺陷基线:读总看最新已提交(脏读);gc 无视活跃快照全回收。

## 实现要点

时间戳:begin 与 commit 共用一条单调时钟,`begin_ts` / `commit_ts` 全序且无重复。

可见性(每条 read 产出 `visibility[]` 一条决策):
1. `source=own_write` —— 本事务写过该键,直接返回自己的值(读己之写),即使
   别的会话之后提交了更新值;
2. `source=snapshot` —— 否则在版本链中二分找 `commit_ts <= begin_ts` 的最右版本,
   带出 `version_ts` 作为证据;
3. `source=none` —— 快照内无可见版本,返回 `null`。

未提交写入只留在 `Transaction.writes`,commit 时才以**单一 commit_ts 原子**进入
版本链,所以并行会话不可能读到半提交状态(部分键可见、部分不可见)。

回收:每条版本链按 `commit_ts` 递增。gc 以最老活跃事务 `begin_ts` 为水位,
保留水位处(含)之前**最新的一个基准版本** + 水位之后的全部版本,更老的悬空版本
释放。无活跃事务时水位落到当前时钟,每条链只留最新版本。因此长时间不结束的对账
会话会把水位钉住,期间的 gc 回收数为 0,而会话结束后立刻推进。

输出新增字段(兼容原 `reads`/`versions`/`gc_stats`/`digest`):
- `visibility[]` —— 逐次读的 `begin_ts`、`source`、`version_ts`;
- `gc_log[]` —— 每次 gc 的 `watermark`、活跃快照列表、`by_key` 与 `reclaimed_versions`;
- `replay[]` —— 按输入下标的执行轨迹(含分配的 ts),可直接重新喂给 `run_ops` 重放;
- `state_digest` —— 覆盖 `versions` + `gc_stats` + `reads` 的终态摘要。

## 验收

`python3 verify.py` 覆盖:

1. **可见性对拍**:3 组交错脚本的每条读都与串行化参考实现(`reference_read`,
   只重放该事务 begin 前已提交的写入 + 自己的写入)逐条一致;且无脏读。
2. **长对账会话**:`s2_long_reconciler` 中会话横跨两次 gc 仍读到 `null`,
   三次水位为 `1, 1, None`,回收统计 `2`;另有专门的"水位之下保留基准版本"用例。
3. **复跑一致**:每组序列同进程复跑、进程级 CLI 复跑,`versions`/`gc_stats`/
   `digest`/`state_digest` 全部一致;三组终态摘要互不相同。
4. **gc 无副作用**:同一脚本去掉 gc 后逐条读结果必须完全相同(证明确实没回收
   快照仍可见的版本)。
5. **可重放**:用输出的 `replay` 轨迹重新执行,终态与摘要完全一致。
6. **随机压测**:5 个种子 × 40 轮随机交错(多事务/多键/回滚/gc)全量对拍。
