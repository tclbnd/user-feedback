# 批量回填的多分片完成屏障与超时收敛

`python3 barrier.py input.json output.json`
验收测试:`TEST_TMP=<可写目录> python3 tests/test_barrier.py`(纯标准库)

input.json: `{"shards":4,"timeout":30,"policy":"release|fail","batches":2,"events":[{"t":0,"shard":0,"batch":1,"type":"arrive|timeout|lost"}...]}`

## 语义(修复后)

- **计数-到达屏障**:批次内凑齐 `shards` 个分片的有效 arrive 立即 `pass` 并关闭屏障。
- **超时一致收敛**:批次仍开放时收到 `timeout` 事件,按 `policy` 做唯一一次批次级裁决
  —— `release` → `released`(放行),`fail` → `fail`(整批失败)。裁决对所有分片相同,
  分片不再按自身到达状态各自下结论,根除判定分裂。
- **失联不挂死**:`lost` 只作历史注解;真正的收敛由 `timeout` 驱动。故障注入中每个失联
  场景都带超时事件,因此不存在永久 `pending`;仅当事件流结束时批次既未凑齐也未超时,
  才如实报 `pending`(在途,而非挂死)。
- **迟到分片滚入下一批**:目标批次已关闭的 arrive 不阻塞它,自动计入下一个仍开放的批次;
  没有可接收批次时记 `late_unrouted`。
- **乱序与重试幂等**:处理前按 `(t, 类型次序, shard, batch)` 去重并规范化排序
  (同刻次序 arrive→lost→timeout),输出只取决于事件集合;重复消息不会滚动或重复计数。

## 输出

`batches[].result ∈ pass|fail|released|pending`,并附 `arrived/missing/lost/closed_at/reason`;
`decisions[]` 为每个 `(shard,batch)` 的裁决(同批恒等);`trace[]` 是逐轮到达/超时/滚动/
重试决策;`digest` 为批次结果+逐分片决策规范化 JSON 的 sha1,跨投递顺序稳定。

trace 动作:`arrived` / `arrived_and_pass` / `marked_lost` / `timeout_released` /
`timeout_fail` / `timeout_ignored_already_closed` / `retry_ignored` /
`late_rollover` / `late_unrouted` / `ignored_batch_closed` / `ignored_out_of_range`。

## 验收证据(fixtures/)

| 序列 | 场景 | 批次结果 | digest(前12位) |
| --- | --- | --- | --- |
| G1 `g1_lost_release.json` | 分片失联 + release,迟到滚入下一批 | released, pass | 3bde4dd48c22 |
| G2 `g2_restart_fail.json` | 失联/重启 + fail,重试幂等 | fail, pass | 035530753bd1 |
| G3 `g3_out_of_order.json` | 整体乱序 + 连续两批超时 | released, pass, released | d46bfc2d46b1 |

每组在测试中以 `in_order / shuffled / retry`(乱序+重复消息)三变体复跑,digest 与
逐分片决策完全一致;`g1_shuffled.json` 是 G1 的乱序+重试独立 CLI 复跑,digest 与顺序版相同。
另含 50 轮随机属性测试(不变量:同批结论恒等、超时批次不 pending、裁决合法、跨投递 digest 一致)。

## 已修复的缺陷基线

失联→pending 挂死;各分片按自身到达状态独立下结论 → 判定分裂;无超时处理;
迟到分片混入当前批;事件顺序/重复消息导致结果不确定。

