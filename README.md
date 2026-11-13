# 遥测累计计数重放

Python 3.9+。运行 `python3 counter.py apply demo.json state.db` 和 `python3 counter.py inspect state.db`。输入是包含 devices 和 samples 的 JSON。devices 将设备映射到 {bits,max_gap,max_rate}，bits 为 16 或 32，max_gap 为允许的最大间隔秒，max_rate 为每秒最大增量。sample 含 device、t（Unix 整秒）、boot（代次）、rev（同设备同秒修订号）和 value。按到达顺序重放。同一设备同一 t 留下 rev 最大的样本。内容相同的重复记录无副作用，同 rev 不同内容应报错。

每台设备以 t 排序，逐对生成 {device,start,end,kind,delta}。依次判定 boot 变化为 reset，间隔大于 max_gap 为 gap，其他情况下若 max_rate×间隔 ≥ 2^bits 或模增量超过 max_rate×间隔则 unknown。reset、gap、unknown 的 delta 为 null。有效情况值下降为 wrap，其余为 normal，delta 是模增量。区间按 device、start、end 排序。已知增量归到结束时间 t//3600 所属小时，包含已知零增量，不分摊跨小时区间。hourly 按 device、hour 排序。

每个可见版本用已消费的输入样本条数标识。apply --upto N 消费累计前缀，重复调用同一前缀不产生变更。inspect --version N 返回该版本，否则返回当前。空状态版本为 0。结果包含 version、intervals、hourly、changes。changes 为从版本 0 起的可见变更批次，每批含 version、remove、upsert，两个列表按区间排序。没有变化时不追加空批次。新插点要撤回旧区间，upsert 两个新区间。更正端点只改实际受影响的区间。传 --crash-before-publish 在本次 apply 可见提交前无清理退出 97，旧快照仍可读，正常重新调用可恢复到无故障真值。

每个新消费的样本前缀都定义一个逻辑版本，即使一次 apply 处理多个样本。changes 按逻辑版本逐样本生成；同一输入用不同 apply 批次消费后，历史版本、变更批次和当前结果必须一致。一次 apply 对外原子公开这些逻辑版本。
