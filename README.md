# ACK 丢失下的批采集

Python 3.9+。执行 `python3 outbox.py ROOT lease p 1`，再 `python3 outbox.py ROOT apply demo.json --lease 1`。每条输入含 partition、offset（从 1 开始）、key、payload（JSON 值），也可含 invalid=true 表示格式错误。每次 apply 只可处理本批出现分区的有效 lease，lease 的 generation 单调递增。旧代次请求退出 3 且不推进任何状态。重复同代 lease 命令无副作用。输入可缓存旧 offset 或有缺口。只可确认从 checkpoint 起连续收到且已可验证持久化的前缀，缺口后可暂存。invalid 和摘要冲突的 offset 进入拒收并可推进前缀。

sink 是独立持久化的幂等接收端，按全局 key 去重。sha256 对 payload 的 canonical JSON UTF-8 算，sort_keys=True、ensure_ascii=False、separators=(",",":")。相同 key 相同摘要无副作用，冲突不覆盖接收端。把待发送批次与稳定标识持久化；恢复时能查清 sink 接受情况。输出 inspect 至少含 leases、checkpoints、acks、sink、rejects。sink 项 {key,sha256} 按 key；rejects 项 {partition,offset,reason} 按 partition,offset，reason 为 INVALID 或 DIGEST_CONFLICT。未出现游标按 0 处理。acks 表示最后一次源确认位置，不得超过 checkpoint。

--crash 在同一批的首个有效边界退出 97，after-outbox（待发状态落盘）、after-sink（接收端已提交但本地未记确认）、after-checkpoint（本地检查点已持久化、源确认前）和 after-ack（源确认后、清理前）。重新 apply 同一批必须继续且幂等。所有源、接收端和故障均本地模拟。

## 实现模型

### 本地待发送记录（state.json 的 outbox）

每条记录把稳定标识绑定在一起：`{batch, partition, offset, key, digest, status}`。

- `batch` 是批次键：对本批行的 `(partition, offset, key, digest)` 规范化身份求 sha256，内容寻址，重放同一输入得到同一批次键。
- `digest` 是 payload 的 canonical sha256；invalid 行不进 outbox，直接进 rejects。
- 状态机区分四种提交状态：
  - `PENDING`：已本地落盘，从未观察到发送；
  - `UNKNOWN`：上次进程可能已发送（重启恢复时 PENDING 先转 UNKNOWN，再向 sink 对账）；
  - `COMMITTED`：sink 已持久化该 key+摘要；
  - `CONFLICT`：sink 中同 key 摘要不同，永不覆盖，同时写 `DIGEST_CONFLICT` 拒收记录。

### 可恢复协议（每个边界独立原子写 + fsync）

1. **after-outbox**：outbox/rejects 落盘。崩溃后重放，已存在的 (partition,offset) 不重复绑定。
2. **after-sink**：先把 `{key, sha256}` 写入独立持久化的 `sink.json` 并 fsync，再把本地记录置 COMMITTED。这个窗口崩溃时本地仍是 PENDING/UNKNOWN，恢复时查 sink：同 key 同摘要判 COMMITTED（重复请求不产生重复提交），同 key 异摘要判 CONFLICT。
3. **after-checkpoint**：只对“已 COMMITTED/CONFLICT 或已拒收”的连续位点前缀推进每分区 checkpoint。
4. **after-ack**：源确认位置只取 checkpoint 值，因此 ack 永不超过 checkpoint；之后只清理被 ack 覆盖的 outbox 记录。

崩溃注入只在边界**有效**时触发（本进程真的写了该状态），所以重跑总能向前推进，不会死循环。

### 租约代次

- `lease p g`：g 小于当前代次退出 3 且无副作用；相等无副作用；大于则单调提升。
- `apply --lease g`：批中每个分区的当前代次必须恰好等于 g，否则退出 3、不推进任何状态。旧实例暂停期间被新实例接管后，重放缓存消息会被代次挡住。

### 多分区位点、水位与失败明细

- 每分区独立按 offset 排序处理；缺口之后的行可暂存（outbox 保留），但 checkpoint/ack 只覆盖连续已解析前缀，顺序契约不变。
- `--watermark N`（默认 1000）：offset 超过 `checkpoint + N` 的行进入 `backpressure` 明细（`BEHIND_WATERMARK`），**不落盘**，源稍后可原样重放。
- `rejects` 为 `{partition, offset, reason}`，reason 为 `INVALID` 或 `DIGEST_CONFLICT`，按 partition,offset 排序；拒收行同样占位点、可推进连续前缀。

### inspect 输出

包含 `leases`、`checkpoints`、`acks`、`sink`、`rejects`（README 要求的五项），另附 `outbox`（含 status，可观察 UNKNOWN/CONFLICT）和 `backpressure`。未出现游标按 0 处理。

## 测试与演练

```sh
python3 -m unittest tests.test_outbox -v     # 11 个验收/单元测试
sh scripts/walkthrough.sh                    # 四点崩溃 + UNKNOWN + 租约切换 + 水位重放
```

walkthrough 会构建一个无故障参考 sink，对四个崩溃点逐一中断、恢复、再重放，并逐键比较事件 key 与摘要，断言重复请求不产生重复提交。

