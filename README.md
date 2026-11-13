# WebVTT 剪辑时间轴

Python 3.9+，使用标准库。命令为 `python3 cut_vtt.py edl.json output.vtt cue-map.json`。EDL 文件含 sources（安全的来源标签到相对文件路径的映射）和 clips。clip 含唯一的 id、source、start_frame、end_frame 和可选 speed=[分子,分母]（默认 [1,1]，范围 1/4 至 4）。源帧率为 30000/1001，起止帧非负且为半开区间。片段按列表顺序相接，输出时长等于源时长除以速度。全部用精确有理秒计算，最后四舍五入到最近毫秒，恰好半毫秒向上。量化后零长 cue 丢弃。

输入 WebVTT 支持 UTF-8、CRLF、NOTE、显式或隐式 ID、可重叠的 cues、REGION 块以及形如 ::cue(.tag) { ... } 的独立 STYLE 规则。保留 cue 设置和普通标签、原文本、换行及实体。不同源同名 class/region 必须重命名为 <来源标签>__<原名>，同源引用与声明一致。源 cue 必须按开始时间非降序。接受 hh:mm:ss.mmm 和 mm:ss.mmm。按与 clip 的交集映射每条 cue。内嵌时间标记保留其后的原文本，仅移除交集之外或量化后不在 cue 严格内侧的标记，标记时间必须严格递增。同一时刻多个标记只留首个。输出按开始毫秒、clip 列表顺序、源 cue 顺序排列，输出 ID 唯一且稳定。

cue-map.json 含 cues，每条记录包含 id、clip、source、source_id（匿名时 null）、line（源 cue 时间行，1 基）、start_ms、end_ms。顺序与导出一致。源文件和 EDL 结构错误以退出码 2 报告文件和行号或 clip 下标。任一验证错误发生时不覆盖已有输出。
