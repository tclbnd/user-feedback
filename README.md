# 设备事件拆表

Python 3.9+，依赖 requirements.txt。运行 `python3 ingest.py ROOT ingest demo.ndjson`。每行对象有 id、rev（正整数）、schema（1 或 2）、device。schema 1 使用 readings=[{channel,value}]，schema 2 使用 samples=[{ch,v}]。输入数组可缺失、显式 null、空数组或非空，分别用 missing、null、empty、present 表示。channel 为非空字符串，值为整数。null 元素或无效子元素导致整事件拒收。源文件和行号用于拒收追踪。

同 id 只公开最高的有效 rev。新版本不合法时保留上一有效版。原样重放无变化。同 id/rev 的有效但不同载荷必须拒绝并保留原已接受事件。拒收记录幂等。公开父、子和拒收表是实际 Parquet，三个表的文件均位于每个已公开版本目录中（由 inspect 返回路径）。父 events 为 {id,rev,device,array_state} 按 id 排序。子 readings 为 {id,rev,ordinal,channel,value} 按 id,ordinal 排序。rejects 为 {id,rev,source,line,path,reason}，path 为类似 /samples/1/v 的指针。可添加列但不删既有列。

inspect 返回 {version,files:{events,readings,rejects}}，路径为 ROOT 相对路径；初始 version=0。inspect --version N 可读历史。首次 ingest 成功发布 version=1；接受有效变化或首次拒收记录才发布新版本。--crash-before-publish 在所有暂存列式表写完、公开版本前退出 97。状态与三个 Parquet 文件必须一起可见。
