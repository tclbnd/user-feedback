# 多源采集的分层预算与原子恢复

Python 3.9+，标准库。先用最小样例核对入口。

```bash
python3 -B sched.py examples/smoke.json output.json
```

样例在 tick 0 派发源 east 的 load 页组 0，成本 2。该 tick 剩余全局预算 1、east 预算 0。tick 1 无派发，预算恢复为 3 和 2。

## 输入

- `horizon` 为 tick 数，从 0 开始。初始预算用 `global_budget` 和 `source_budget`，租户权重用 `weights`。预算是非负整数，权重和页组成本为正整数。
- `events` 每项有 `tick`、`seq`、`kind`。同一 tick 先按 `seq` 应用全部事件，再派发。输入可乱序，重复投递保留原 `seq`。
- `arrival` 事件另有 `source`、`tenant`、`task`、`part`、`cost`。不同 `seq` 的两条到达也可能指向同一页组，以 `(source, task, part)` 去重。首次出现确定该页组的归属和成本。
- `global_budget`、`source_budget` 事件携带新的 `budget`，后者另有 `source`。更新从当 tick 起生效。 `pause` 事件用 `source` 和布尔 `paused` 控制单源暂停和恢复。

## 派发与恢复

每个 tick 重装当时的两级预算。轮转首次从最小租户 ID 起步，之后按 ID 循环。开始访问一个非空队列时加一次权重，再从该租户符合两级预算和赤字的页组中按 `(到达 tick, task, part)` 逐组派发，直到该队列无可派发组。每次派发扣同量的两级预算和赤字，积压清空时重置赤字。

全局预算耗尽时立即结束 tick。若当前租户仍有积压且赤字大于零，下个 tick 从当前访问继续，这次访问不再加权重。否则下个 tick 从下一租户开始。没有耗尽全局预算时，当前访问完成就推进到下一租户，连续访问一圈都无派发就结束 tick。待派发页组、赤字和轮转位置都跨 tick 保留。

用预算每 tick 只够一个单位成本页组、权重分别为 1 和 2 的两个持续积压租户快速检查。若 ID 依次为 a 和 b，前六次派发应为 a、b、b、a、b、b。

`--state PATH` 指向可复用的本地检查点。恢复输入包含相同事件，可更换输入顺序并重送已有事件。检查点应能恢复原有 tick、轮转位置和已提交的扣账。

`--fail-at before-debit:N` 在第 N 次派发扣账前退出 75，当时的检查点可恢复到同一待派发组。`--fail-at after-commit:N` 在第 N 次派发与检查点提交后退出 75。`--fail-at after-restore` 在读取已有检查点后、执行下一次更新前退出 75。N 从 1 开始。注入结束后去掉该参数续跑。

## 输出

- `dispatch` 按提交顺序记录每项的 `tick, source, tenant, task, part, cost`。
- `balances` 每 tick 一项，记录派发结束后的 `tick, global, source`。 `source` 为数据源到剩余预算的映射。
- `service` 记录各租户累计消耗，`paused_ticks` 记录各源暂停的 tick 数。
- `backlog_segments` 按租户记录逐个连续积压段的服务量。 `waits` 每项给出页组定位和原因，原因值为 `budget`、`paused` 或 `rotation`。
- `digest` 对其余输出字段按键排序、紧凑 JSON 序列化后计算 SHA-256。原始输入顺序和重复传递次数不影响摘要。

起始实现能执行最小样例。目前采用队头单次派发，检查点只记派发列表，仍缺赤字轮转、稳定去重和原子续跑。
