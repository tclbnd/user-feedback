# ACK 丢失下的批采集

Python 3.9+。执行 `python3 outbox.py ROOT lease p 1`，再 `python3 outbox.py ROOT apply demo.json --lease 1`。每条输入含 partition、offset（从 1 开始）、key、payload（JSON 值），也可含 invalid=true 表示格式错误。每次 apply 只可处理本批出现分区的有效 lease，lease 的 generation 单调递增。旧代次请求退出 3 且不推进任何状态。重复同代 lease 命令无副作用。输入可缓存旧 offset 或有缺口。只可确认从 checkpoint 起连续收到且已可验证持久化的前缀，缺口后可暂存。invalid 和摘要冲突的 offset 进入拒收并可推进前缀。

sink 是独立持久化的幂等接收端，按全局 key 去重。sha256 对 payload 的 canonical JSON UTF-8 算，sort_keys=True、ensure_ascii=False、separators=(",",":")。相同 key 相同摘要无副作用，冲突不覆盖接收端。把待发送批次与稳定标识持久化；恢复时能查清 sink 接受情况。输出 inspect 至少含 leases、checkpoints、acks、sink、rejects。sink 项 {key,sha256} 按 key；rejects 项 {partition,offset,reason} 按 partition,offset，reason 为 INVALID 或 DIGEST_CONFLICT。未出现游标按 0 处理。acks 表示最后一次源确认位置，不得超过 checkpoint。

--crash 在同一批的首个有效边界退出 97，after-outbox（待发状态落盘）、after-sink（接收端已提交但本地未记确认）、after-checkpoint（本地检查点已持久化、源确认前）和 after-ack（源确认后、清理前）。重新 apply 同一批必须继续且幂等。所有源、接收端和故障均本地模拟。
