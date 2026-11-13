# 遥测累计计数重放

Python 3.9+。运行 `python3 counter.py apply demo.json state.db` 和 `python3 counter.py inspect state.db`。输入是包含 devices 和 samples 的 JSON。devices 将设备映射到 {bits,max_gap,max_rate}，bits 为 16 或 32，max_gap 为允许的最大间隔秒，max_rate 为每秒最大增量。sample 含 device、t（Unix 整秒）、boot（代次）、rev（同设备同秒修订号）和 value。按到达顺序重放。同一设备同一 t 留下 rev 最大的样本。内容相同的重复记录无副作用，同 rev 不同内容应报错。

每台设备以 t 排序，逐对生成 {device,start,end,kind,delta}。依次判定 boot 变化为 reset，间隔大于 max_gap 为 gap，其他情况下若 max_rate×间隔 ≥ 2^bits 或模增量超过 max_rate×间隔则 unknown。reset、gap、unknown 的 delta 为 null。有效情况值下降为 wrap，其余为 normal，delta 是模增量。区间按 device、start、end 排序。已知增量归到结束时间 t//3600 所属小时，包含已知零增量，不分摊跨小时区间。hourly 按 device、hour 排序。

每个可见版本用已消费的输入样本条数标识。apply --upto N 消费累计前缀，重复调用同一前缀不产生变更。inspect --version N 返回该版本，否则返回当前。空状态版本为 0。结果包含 version、intervals、hourly、changes。changes 为从版本 0 起的可见变更批次，每批含 version、remove、upsert，两个列表按区间排序。没有变化时不追加空批次。新插点要撤回旧区间，upsert 两个新区间。更正端点只改实际受影响的区间。传 --crash-before-publish 在本次 apply 可见提交前无清理退出 97，旧快照仍可读，正常重新调用可恢复到无故障真值。

每个新消费的样本前缀都定义一个逻辑版本，即使一次 apply 处理多个样本。changes 按逻辑版本逐样本生成；同一输入用不同 apply 批次消费后，历史版本、变更批次和当前结果必须一致。一次 apply 对外原子公开这些逻辑版本。

## 区间诊断与观测覆盖

`kind` 即每段区间的诊断来源，按以下顺序独立于到达次序判定（只看两端样本与设备配置）：

- `reset`：`boot` 代次变化，显式重启，增量不可观测，`delta=null`。
- `gap`：同代但间隔超过 `max_gap`，断采窗口外可能发生任意事，`delta=null`。
- `unknown`：同代且在窗口内，但 `max_rate×间隔 ≥ 2^bits`（整个模周期都装得下，回绕与重启无法区分）或模增量超过速率预算，来源规则无法界定真实增量，`delta=null`。
- `wrap`：同代、窗口内、值下降且可证实为模回绕，`delta` 为模增量。
- `normal`：同代、窗口内、值不下降，`delta` 为模增量（含可证实的零增量）。

可证实增量（`normal`/`wrap`）与不可观测区间（`reset`/`gap`/`unknown`）在 `intervals` 中分别以数值 `delta` 与 `null` 输出；只有前者进入 `hourly`。跨小时区间不做分摊，整段增量归到结束样本 `t//3600` 的小时；回绕腿若恰好结束在小时边界（如 `t=3600`），归边界后的小时。`unknown` 永不产生小时条目，因此小时汇总之和恒等于所有非 null 增量之和，未知数据不会被隐含分配。

迟到点（含乱序与更高 `rev` 更正）按 `(device, t)` 插入有序序列：新点落在两个旧邻点之间时，change 批次的 `remove` 精确撤回被跨越的旧区间，`upsert` 给出两条新邻接区间；更正端点只重建实际受影响的邻接区间。批次先按 key 删除再 upsert，使任意历史版本都能由 changes 折叠重现。

## 持久化

状态文件原子写入（同目录临时文件 + `os.replace` + fsync），保存读游标 `cursor`、每设备有序样本与时间戳索引、当前区间集合和修订日志 `changes`。`--crash-before-publish` 在可见提交前以退出码 97 退出且不做清理，磁盘上保留旧快照、无临时文件残留，正常重放收敛到同一真值。

## 测试

`python3 -m unittest test_counter -v`。测试内含独立预言机：对同一流做全量排序后逐对重新解释，并逐前缀重建区间、小时汇总与变更批次，与引擎的增量撤回/插入结果比对；另含 16/32 位回绕、重启、长断采、乱序、同 rev 冲突、幂等前缀、发布中断重放、回绕与小时边界重合迟到点，以及随机模糊与分批消费不变性测试。
