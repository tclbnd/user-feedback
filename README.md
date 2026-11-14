# 直播 DVR 环形片段缓冲一致性
`python3 dvr.py req.json output.json`
req.json: `{"segs":200,"window":8,"crash_at":50,"readers":3,"speed":2}`
- 片段 seq 连续写入+清单原子更新: 先片段可读再添引用; 窗口滑动先确认新片段再移除旧引用
- crash_at 处模拟崩溃,重启从 state.json 恢复窗口(与未崩溃基线条目一致)
- 读者清单内片段全部可读; 3 读者见相同快照
- 输出 `{"manifest":[{seq,visible}],"dangling":n,"recovered":bool,"snapshots":{r:[segs]}}`
缺陷基线: 清单先写片段后写,崩溃丢窗口状态.
