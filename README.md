# 时钟回拨下单调序列号生成
Python3.9+ stdlib. `python3 idgen.py req.json output.json`
req.json: `{"clock":[0,1,2,1.5,1.4,50,49,100],"batch":2,"state":"state.json"}`
- ID = ts_ms*2^12 + seq(0..4095); 时钟回拨时 ts 位不得回退(用 last_ts 补), seq 耗尽等待下一 ms
- 回拨 >500ms 用漂移位(时间位+偏移)接管并记 alert; state 持久化 last_ts,重启读回保证不回退
- 输出 `{"ids":[...],"meta":[{ts,seq,src:"normal|wait|drift"}],"alerts":[...]}`

## 实现（修复后）

### 不变量（任意并发度 / 批大小下成立）
1. `id` 按生成序**严格单调递增且全局唯一**，上游分片路由不会错乱。
2. **逻辑时间位 `last_ts` 单调不减**：物理时钟回拨时新 ID 的时间位不小于回拨前。
3. 同一毫秒序号 `0..4095`，耗尽走等待；漂移期耗尽走逻辑跳毫秒（不睡眠）。

### 时钟回拨两级策略（阈值 `max_backward_ms`，默认 500ms）
| 情形 | 策略 | src | 告警 |
|---|---|---|---|
| 回拨幅度 ≤ 阈值 | **等待**：拒绝回退时间位，阻塞到物理钟追平 `last_ts` | `wait` | `CLOCK_BACKWARD_WAIT`(WARN) |
| 回拨幅度 > 阈值 | **漂移位接管**：`drift_offset = last_ts - phys`，逻辑时间位 `phys + offset` 继续前进 | `drift`(meta.drift=1) | `DRIFT_TAKEOVER`(ERROR) |
| 漂移期物理钟再回退 | 加大偏移继续接管 | `drift` | `DRIFT_EXTENDED`(WARN) |
| 物理钟追平逻辑时间位 | 退出漂移，交还时间位 | `normal` | `DRIFT_RECOVER`(WARN) |
| 序号 0..4095 耗尽 | 等待到下一毫秒（漂移期内逻辑跳 1ms） | `wait`/`drift` | `SEQUENCE_EXHAUSTED_WAIT`(WARN) |

### 持久化与跨进程重启
- 原子写（临时文件 + `os.replace` + fsync）`last_ts / last_seq / last_id / drift_offset`。
- 启动读回持久化状态：物理钟落后 ≤ 阈值则 `STARTUP_BACKWARD_WAIT` 等待追平；
  落后 > 阈值则 `STARTUP_DRIFT_TAKEOVER` 启动即漂移，逻辑时间位从持久化值继续，
  保证**重启后首 ID 时间位 ≥ 关闭前最后值**。状态文件损坏时降级为无状态启动。

### 逐 ID 输出
`meta[i] = {ts(逻辑时间位), seq(序号), src(normal|wait|drift), drift(0|1), phys(物理钟)}`，
且 `ids[i] == meta[i].ts * 4096 + meta[i].seq`。线程安全由单把锁保证，
分配结果只取决于调用次数与时钟轨迹，与线程数、批大小无关。

## 验收
```
python3 src/test_idgen.py
```
- 用例1：注入 100ms 回拨（等待）/ 50ms 突跳 / 600ms 超限回拨（漂移），严格单调不重复，回拨期时间位不回退。
- 用例2：序号耗尽等待下一毫秒；漂移期耗尽逻辑跳毫秒且不依赖物理钟。
- 用例3：16 线程并发取 10^6 ID，全局唯一、按生成序单调（~10^6 ID/s）。
- 用例3b：16 线程取号与回拨/突跳注入交织，等待与漂移并发发生仍单调唯一。
- 用例4：进程内 + 跨进程 CLI 重启，首 ID 时间位 ≥ 关闭前最后值。
- 用例5：batch=1/5/16 同一时钟轨迹产出逐 ID 一致；并发与串行同起点同 ID 集合。
- 用例6：CLI 契约与 `ids/meta/alerts/stats` 输出结构。

