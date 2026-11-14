# 时钟回拨下单调序列号生成

Python 3.9+，纯标准库，无第三方依赖。

```
python3 idgen.py req.json output.json     # CLI（保持原接口）
python3 idgen.py --help                   # 无参数时打印用法
python3 selftest.py                       # 22 个单元/验收测试
python3 realtest.py                       # 真实系统时钟下 16 线程 10^6 压测
```

## ID 结构

```
id = (ts_ms << 12) | seq          seq ∈ [0, 4095]   ts_ms 为逻辑时间位
```

`ts_ms` 是**逻辑**时间位：正常情况下等于物理毫秒，回拨时被冻结或加漂移偏移。
因此下游用 `id >> 12` 做 `hash(id) % N` 之类的时间分片，不会因 NTP 回拨而错乱。

## 三条硬不变量

| 不变量 | 保证方式 |
|---|---|
| 严格单调（后发 ID 必大于先发） | 所有状态迁移在同一把互斥锁内完成 |
| 全局唯一（含跨进程重启） | 每进入新毫秒先落盘水位线；重启按水位线续发 |
| 时间位不倒退 | 物理时钟回拨时拒绝回退时间位，冻结或加偏移 |

## 回拨三级策略

| 场景 | 策略 | `src` | 告警 |
|---|---|---|---|
| `delta <= max_rollback_ms`（默认 500ms） | 拒绝回退时间位，冻结在 `last_ts` 上继续消费序号，等物理钟追平 | `wait` | `clock_rollback_hold` |
| 同一逻辑毫秒序号耗尽 | 等待下一毫秒（真实 `sleep`，醒来重读时钟） | `wait` | `seq_exhausted_wait` |
| 等待超过 `max_wait_ms` 仍无新毫秒 | 强制把逻辑时间推进 1ms（仍单调/唯一） | `wait` | `wait_timeout_forced_advance` |
| `delta > max_rollback_ms` | 漂移位接管：`逻辑时间 = wall + offset`，继续前进 | `drift` | `rollback_exceeded_drift_engaged` |
| 漂移期内再次回拨 | 延长偏移 | `drift` | `drift_extended` |
| 物理钟追平逻辑前沿 | 偏移退役，回到正常路径 | `normal` | `drift_retired` |

告警按“条件”去重：`seq_exhausted_wait` / `wait_timeout_forced_advance` /
`drift_extended` 等持续状态整条生命周期只报一次，不会按 ID 数量刷屏。

## 跨进程重启

状态文件是带 `version` + `sha256` 校验的 JSON，写入走 `tmp + fsync + os.replace`，
并对同一路径加 `flock` 排他锁（同一状态文件不允许两个活着的生成器）。

- **正常 `close()`**：落盘精确 `(last_ts, seq, last_id)`，重启后首 ID 严格大于关闭前最后值。
- **崩溃（未 close）**：磁盘上只有“水位线”（整个毫秒按已用尽记），重启从 `last_ts + 1`
  续发，可能跳过若干号但**绝不重发**。
- **重启后再崩溃**：`closed=true` 的中段毫秒在恢复时会重新被登记为水位线，
  避免第二次崩溃按旧 `seq` 重放同一批 ID。
- **校验失败**：抛 `StateError` 并拒绝启动（fail-closed）；绝不静默放宽单调性。
- **`clock_id` 不一致**：判定为另一个物理时钟，不继承旧下限，告警 `clock_id_mismatch`。
- **旧版状态**（基线的 `{"last": ms}`）：按水位线语义保守恢复，告警 `legacy_state_adopted`。

## 输入 / 输出

```json
// req.json
{"clock": [0, 1, 2, 1.5, 1.4, 50, 49, 100],   // 秒；缺省则用系统时钟
 "batch": 2, "count": 8, "state": "state.json", "clock_id": "node-a",
 "max_rollback_ms": 500, "max_wait_ms": 10}
```

```json
// output.json
{"ids": [...],
 "meta": [{"index":0,"id":0,"ts":0,"seq":0,"src":"normal","wall":0,"drift":0}, ...],
 "alerts": [{"code":"clock_rollback_hold","message":"...","detail":{"delta":100}}],
 "stats": {"issued":8,"normal":5,"wait":1,"drift":2,"forced_advance":0, ...},
 "state": {"last_ts":100000,"seq":0,"last_id":409600000,"drift":0,...}}
```

`meta` 逐 ID 给出**时间位**、**序号**与**来源标记**（`normal|wait|drift`），
另附 `wall`（读到的物理毫秒）与 `drift`（当时的偏移），便于在线排查分片错乱。

## 批大小 / 并发无关

每个 ID 恰好读取一次注入时钟，且状态迁移是单步原子操作；`take(n)` 与 `n 次 take(1)`
走同一条状态机，因此输出序列与批大小、线程数无关（`test_batch_size_invariance`
逐元素比对 2/3/7/64/1000/2000 六种批大小）。

## 验证结果

- 注入 100ms 回拨 + 50ms 突跳：ID 严格单调、无重复，回拨期内时间位 ≥ 回拨前。
- 16 线程 × 10⁶：全局唯一、按生成序（锁内 `order`）严格递增；
  真实系统时钟实测约 **1.6M id/s**，重启后首 ID 时间位 ≥ 关闭前最后值。
- 4000 步随机回拨/突跳风暴：严格单调、时间位不回退。

