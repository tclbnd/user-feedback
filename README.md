# 双设备时钟校准

Python 3.9+。运行 `python3 align.py demo.json output.json`。输入 devices 按设备列出同一采集序列上的 sync（已检测出的候选同步标记）和 probes（待标定时间戳）。时间单位为秒。sync 项为 {seq,tick,reference}，seq 是跨重启严格递增的采集顺序号，tick 是设备时钟，reference 是候选参考时刻。可能存在缺失标记、误配标记、重复标记和重复 tick。probes 项 {seq,tick,id}。只用本地数据完成校准。每台设备的合并输入按 seq 排序，同 seq 的 probe 与 sync 使用同一个 tick。按采集顺序识别明显的时钟回拨，即当前 tick 比前一位置低超过 0.5 秒，启动新的 epoch。长间隔不等于回拨。可在同 epoch 中拟合 offset 与速率。设备标称速率漂移小于 500 ppm，可靠同步标记抖动小于 1.5 ms，错配误差至少 0.15 秒。每个可靠区间至少 5 个独立标记，极端误配不超过 20%，时间跨度至少 10 秒。覆盖范围之外至多允许向前后外推 30 秒，其余 probe 的 reference 为 null。缺锚点的 epoch 不能沿用旧模型。

输出 mapped 按设备与原 probe 列表顺序排列，每项 {device,id,epoch,reference}，epoch 从 0 开始。epochs 至少包含 device、epoch、coverage（null 或 [首个可靠 tick,最后可靠 tick]）、slope、offset、residual_ms（p95 绝对残差）；无可靠拟合后三项为 null。可增加异常统计。已标定 probe 作为帧和采样对齐索引，要求 40 分钟回放 p95 ≤ 5 ms。参考时钟须在跨重启的有观测段保持全局顺序。用缺标记区间测试拒绝输出。
