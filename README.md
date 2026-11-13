# ACK 丢失下的批采集

Python 3.9+。执行 `python3 outbox.py ROOT lease p 1`，再 `python3 outbox.py ROOT apply demo.json --lease 1`。每条输入含 partition、offset（从 1 开始）、key、payload（JSON 值），也可含 invalid=true 表示格式错误。每次 apply 只可处理本批出现分区的有效 lease，lease 的 generation 单调递增。旧代次请求退出 3 且不推进任何状态。重复同代 lease 命令无副作用。输入可缓存旧 offset 或有缺口。只可确认从 checkpoint 起连续收到且已可验证持久化的前缀，缺口后可暂存。invalid 和摘要冲突的 offset 进入拒收并可推进前缀。

sink 是独立持久化的幂等接收端（`ROOT/sink.json`，与客户端 `ROOT/state.json` 分开落盘），按全局稳定事件键 `partition#offset` 去重。sha256 对 payload 的 canonical JSON UTF-8 算，sort_keys=True、ensure_ascii=False、separators=(",",":")。相同 key 相同摘要无副作用，冲突不覆盖接收端。把待发送批次与稳定标识持久化；恢复时能查清 sink 接受情况。输出 inspect 至少含 leases、checkpoints、acks、sink、rejects。sink 项 {key,sha256} 按分区与数值位点；rejects 项 {partition,offset,reason} 按 partition,offset，reason 为 INVALID 或 DIGEST_CONFLICT。未出现游标按 0 处理。acks 表示最后一次源确认位置，不得超过 checkpoint。

## 可恢复协议与待发送记录

每条待发送记录绑定稳定事件键（`partition#offset`）、批次键（整批 canonical 行的 sha256，同输入重放稳定）与 payload 摘要，状态机为：

- UNCOMMITTED：待发记录已落盘，尚未向 sink 提交；
- UNKNOWN：已写入 UNKNOWN 标记并尝试提交，故障后无法确认结果。重启时查询独立 sink：已持久化则收敛 COMMITTED，未持久化则退回 UNCOMMITTED 重投，绝不盲目重复提交；
- COMMITTED：sink 已确认 key+摘要持久化；
- CONFLICT：同事件键的 payload 摘要与 sink 既有值冲突，写独立拒收且不覆盖接收端。

提交顺序严格按分区、offset 升序；检查点只覆盖已验证持久化的连续前缀，缺口后的有效行进入 buffered 暂存，补齐后按原顺序重放。INVALID 行在进入缓存前即独立审计，可被单独重放审计而不改变顺序契约。缺口缓存超过水位容量（默认 64）时，在连续前缀安全落盘后以退出码 4 回压，stderr 给出 BACKPRESSURE 失败明细。

## 退出码

- 0 成功；2 输入无法读取；3 旧/未知租约代次（围栏，无副作用）；4 水位回压；97 模拟故障中断。

## 故障点

`--crash` 在同一批的首个有效边界退出 97，after-outbox（待发状态落盘）、after-sink（接收端已提交但本地未记确认）、after-checkpoint（本地检查点已持久化、源确认前）和 after-ack（源确认后、清理前）。重新 apply 同一批必须继续且幂等；after-checkpoint 后重启会把 acks 追到已持久化的 checkpoint 但永不越过它。所有源、接收端和故障均本地模拟，所有写入采用临时文件 + fsync + 原子 rename。inspect 另含 outbox、buffered、watermarks、stats（sink_accepts / sink_idempotent_hits / digest_conflicts）与 crashed，便于审计。验收测试：`python3 acceptance_test.py`。
