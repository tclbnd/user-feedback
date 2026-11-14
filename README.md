# 事件溯源聚合重放修复

Python 3.9+，标准库。运行 `python3 replay.py events.json output.json`。

events.json 为事件流：`{"events":[{"seq":0,"type":"credit","v":1,"data":{...}},...]}`。同一聚合的事件按 `seq` 全序排列；`v` 是事件载荷的版本号（1 为最旧）。

版本语义：

- `v=1 credit/debit`：`data.amount` 为分（整数分）。
- `v=2 credit/debit`：`data.cents` 为分；`data.currency` 必填，非 `CNY` 计入 `rejects`。
- `v=3 credit/debit`：`data.amount` 为字符串十进制的**元**，需换算为分（"1.50"→150）；允许缺失 `currency`。
- `v=4 rename`：`data.name` 写入聚合 `name`；任意版本都支持。
- 未知 `type` 或 `v`：追加到 `rejects`，含 `{"seq","reason"}`；**不得**静默跳过或影响状态。

折叠规则：状态 `{"balance_cents":int,"name":str,"applied":int}`（applied=已应用事件数）。credit 加、debit 减。输出 `{"state":...,"rejects":[...],"applied_by_version":{"1":n,...}}`。

快照：`--snapshot snap.json` 时若文件存在则从快照恢复（快照含 `last_seq` 与 `state`），只折叠 `seq>last_seq` 的事件，完成后写回更新后的快照。从快照恢复的结果必须与全程折叠逐字段一致。命令行另支持 `--fail-after n`：处理到第 n 个事件后以退出 3 中止，用于崩溃恢复测试。

现有实现的缺陷：v≥2 的事件被直接按 `data.amount` 读取导致崩溃或按元当分入账，v3 缺 `currency` 被拒收，未知版本被静默跳过，快照恢复的 `applied` 计数与余额错位。
