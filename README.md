# 多源采集字段变更与隔离补录

Python 3.9+，标准库。运行最小样例。

```bash
python3 -B crawl.py examples/smoke.json result.json
```

样例的 `rows` 中有一条 east/day1 的 invoice-1，`amount_cents` 为 200，`cursor["east/day1"]` 为 1。

## 输入事件

`initial_rules` 是已生效的规则列表，每条有 `source`、`schema` 和 `rule`。一个 `rule` 给出 `amount_field`、整数 `multiplier`、`enum_field` 及字典 `enum`。每次乘法都在整数域内完成。校验金额字段是整数、枚举存在，禁止将空值推断为零。

`events` 按整数 `id` 升序处理。同 id 同内容重复时只处理一次，允许按任意文件顺序到达。事件类型有四种。

- `row` 携带 `source`、`partition`、`seq`、`key`、`schema`、`revision`、`op` 和 `data`。源序号从 1 开始。来源、分区与序号共同标识采集行。同编号但内容不同产生 `sequence_conflict`，保留先前记录。
- `approve` 携带 `source`、`partition`、`schema`、`rule`、`rule_sha256`、`from_seq`、`through_seq`。规则摘要是规则对象按键排序、紧凑 JSON 编码后的 SHA-256。摘要不符的审批只写拒绝审计，继续处理其余事件。同分区同版本的审批只影响闭区间内的行。补录按 seq 递增。存在多条批准时选事件 id 最大且未撤销的规则。
- `revoke` 携带 `approval_id`，撤销该审批对后续及待补录行的授权。已提交行保留，后续数据用它自己的有效映射。
- `row` 的 `op` 是 `upsert` 或 `delete`。删除无需数据字段，但仍要有有效版本。用 `(source, partition, key)` 查当前修订，只有严格更新的修订能改变业务值。删除保留修订号。晚到的低修订只能产生可追溯的决定。

各分区的游标是从 1 起连续持久接收的最大序号。隔离持久化也属于接收，序号有洞时游标不跨越。

## 输出与恢复

- `rows` 和 `tombstones` 分别列出现值和确认删除，按 `(source, partition, key)` 排序。行包含 `source, partition, key, revision, amount_cents, status`，撤回项只保留前四个字段。
- `quarantine` 保留未解决事件的 `source, partition, seq, schema, reason, column, data`。按来源、分区和 seq 排序。常见原因是 `unknown_schema`（column 为 null）、`invalid_amount`（column 为金额字段名）、`invalid_enum`（column 为枚举字段名）。同编号冲突列到 `conflicts`，记录 `source, partition, seq, id, reason`。
- `cursor` 用 `source/partition` 作键，`backfilled` 列出每项补录的 `id, source, partition, seq, approval_id, outcome`。结果可能为 `applied`、`deleted` 或 `stale_revision`。
- `audit` 按实际决策顺序保留 `id, action`。涉及采集行时再给 `source, partition, seq, schema`，补录带 `approval_id`。同一事件重送不能复制决定。业务行与隔离、补录、游标和审计一起提交。

`--state PATH` 持久保存完整进度。验收会打乱事件并重复投递，然后用同一路径恢复。
`--fail-at after-row:N` 在第 N 个输入事件的业务更新后、事务提交前退出 75。
`--fail-at before-commit:N` 在同一事件写入检查点前退出 75。
`--fail-at before-ack:N` 在同一事件提交后、返回确认前退出 75。这里 N 是输入事件 id。恢复时去掉故障参数。

## 实现要点

- 状态文件是只追加日志：首行记录 `initial_rules`，其后每个事件一行，逐行 flush+fsync。一行写全（带换行）才算提交；恢复时丢弃残缺尾行，所以磁盘上总是整数个完整提交，业务行、隔离、冲突、游标、补录与审计由同一条记录一起生效。
- 派生视图由已接收事件按 `id` 升序重放得到。乱序到达的低 `id` 会把视图标记为陈旧，并在最后整体重放，因此结果只取决于已接收集合，而与到达顺序、分块方式和恢复次数无关。
- 映射选择：同一 `(source, partition, schema)` 内，闭区间覆盖该行且未撤销的审批优先，取事件 `id` 最大者；区间之外退回 `initial_rules` 中已生效的 `(source, schema)`；都没有则 `unknown_schema` 隔离。审批只对其绑定的版本生效，撤销后不再用于后续或待补录行，已提交行保留。
- 收到即确认：正常行、隔离行和冲突行都会推进所属分区的连续游标；映射选择失败的删除也隔离，不改变业务值。补录只重放隔离行，不重复计入接收。
- 金额全程整数分；字符串、空值、布尔和非整数一律 `invalid_amount`，不做任何隐式转换。
- 同一业务主键只有严格更大的 `revision` 能改变业务值，删除也占修订号，因此补录的旧记录不能让已删主键复活。

测试：`python3 -B -m unittest discover -s tests -v`。覆盖三源混合轨迹、参考值逐事件核对、补录前后健康分区一致、三个故障点恢复、乱序/重复/重分块等价，以及残缺尾行恢复。
