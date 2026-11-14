# 事件溯源聚合重放修复

Python 3.9+，标准库。运行 `python3 replay.py events.json output.json`。

events.json 为事件流：`{"events":[{"seq":0,"type":"credit","v":1,"data":{...}},...]}`。同一聚合的事件按 `seq` 全序排列；`v` 是事件载荷的版本号（1 为最旧）。

版本语义：

- `v=1 credit/debit`：`data.amount` 为分（整数分）。
- `v=2 credit/debit`：`data.cents` 为分；`data.currency` 必填，非 `CNY` 计入 `rejects`。
- `v=3 credit/debit`：`data.amount` 为字符串十进制的**元**，需换算为分（"1.50"→150）；允许缺失 `currency`。
- `v=4 rename`：`data.name` 写入聚合 `name`；任意版本都支持。
- 未知 `type` 或 `v`：追加到 `rejects`，含 `{"seq","reason"}`；**不得**静默跳过或影响状态。

折叠规则：状态 `{"balance_cents":int,"name":str,"applied":int}`（applied=已应用事件数）。credit 加、debit 减。输出 `{"state":...,"rejects":[...],"applied_by_version":{"1":n,...},"migrations":[...]}`。

快照：`--snapshot snap.json` 时若文件存在则从快照恢复（快照含 `last_seq` 与 `state`），只折叠 `seq>last_seq` 的事件，完成后写回更新后的快照。从快照恢复的结果必须与全程折叠逐字段一致。命令行另支持 `--fail-after n`：处理到第 n 个事件后以退出 3 中止，用于崩溃恢复测试。

## 实现要点

**迁移先于折叠。** 每个事件先经 `UpcasterRegistry` 中按 `(action, from_version)` 注册的 upcaster 链迁移到该动作的规范版本（credit/debit → v3 的“十进制元”载荷，rename 载荷与版本无关），折叠器只处理迁移后的载荷。链式迁移：v1 `amount`(分) → v2 `cents`+`currency` → v3 `amount`(元字符串)，`path` 记录实际经过的版本序列（如 v1 事件为 `[1,2,3]`）。

**缺失迁移路径显式报错。** 链中断、起始版本未知、版本非整数、或版本高于规范版本时抛 `MissingUpcasterPath`，折叠器把它写进 `rejects`，reason 形如 `no-upcast-path:v9->v3`，`seq` 定位到具体事件；状态与计数完全不受影响。迁移过程中的数据问题（缺字段、非法金额、非 CNY 币种）同样进 `rejects`（`invalid-amount` / `unsupported-currency` / `invalid-name`），不会崩溃、也不会按错误语义入账。

**前缀确定性与快照。** 折叠是“迁移后事件前缀”的纯函数，同一前缀总得到同一状态；快照只保存已迁移事件的结果（`state`、`rejects`、`applied_by_version`、`migrations`、`last_seq`、`name_seq`），不缓存任何旧格式原始载荷。恢复时按 `seq>last_seq` 继续折叠即可与全程折叠逐字段一致；旧版精简快照（仅 `state`+`last_seq`）也能恢复，缺失的迁移记录从已覆盖前缀重新计算。

**并行重放与合并。** 模块无全局可变状态，`fold_events` / `ReplayEngine` / `fold_shards` 可对不同流并发调用互不干扰。`fold_shards` 把有序流切成连续分片并行折叠，`merge_results` 合并：余额与 `applied` 相加，`rejects`/`migrations` 按 `seq` 稳定排序，`name` 取最后一个已应用的 rename（用 `name_seq` 定位，而非简单取最后一片），因此多片合并与单流折叠结果一致。

**迁移记录。** `migrations` 逐事件输出 `{"seq","type","path"}`，只含成功迁移并折叠的事件；库内可用 `replay.iter_replay_records(result)` 逐条遍历。命令行新增 `--shards n`：把流切成 n 个连续分片并行折叠后合并，用于自检合并等价性。

## 测试

```
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

覆盖：与参考输出逐字段一致、三代事件各自语义、迁移路径记录、缺路径报错与序号定位、独立参考实现的交叉校验、前缀稳定性、任意位置快照续跑、两片/多片并行合并、并发流互不干扰、崩溃（`--fail-after`）后续跑、畸形事件隔离。

## 原实现缺陷（已修复）

- v≥2 的事件被直接按 `data.amount` 读取，导致崩溃或按元当分入账。
- v3 缺 `currency` 被拒收（应接受）。
- 未知版本被静默跳过（应显式报错并定位 `seq`）。
- 快照恢复的 `applied` 计数与余额错位；快照未保存 `rejects`/`applied_by_version`。
- `--fail-after` 中止时不落盘快照，崩溃恢复无法续跑。
