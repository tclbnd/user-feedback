# 帧化音频容器修复

Python 3.9+，标准库。运行 `python3 unframe.py input.zaf output.json`。`.zaf` 流以 8 字节魔数 `ZQAF0001` 开头，其后为顺序排列的帧。每帧头部 12 字节，依次为 `seq`(u32be)、`length`(u32be，载荷字节数)、`crc32`(u32be，对 `seq|length|payload` 的字节串计算 zlib.crc32)。载荷为任意字节。

流中可能出现三类损坏：**bad-header**（头部字段非法，如 length 超过剩余字节或与后续内容矛盾）、**bad-payload**（头部可解析但 CRC 不匹配）、**truncated**（流在帧中部结束）。实现必须在遇到损坏时不整体失败：

- 对每个可解析的头记录 `{"seq","offset","length","status"}`，`offset` 为帧头起始字节；`status` ∈ `ok / bad-payload`。
- 无法从某偏移继续解析时，向 `gaps` 追加 `{"offset","reason"}`，`reason` ∈ `bad-header / truncated`，并尝试在后续字节中重新对齐：从 offset+1 起寻找下一个能通过 CRC 的完整帧；找到则继续，找不到则报告 `unrecoverable` 并以 offset 标记。
- `regions` 列出可解码区间 `{"start","end","frames"}`（帧头偏移、载荷结束偏移、区间内 ok 帧数）。bad-payload 帧的载荷**不得**计入 region，其头偏移仍出现在 frames 表。
- `summary` 含 `frames_total`、`frames_ok`、`frames_bad`、`gaps`、`bytes_recovered`（全部 ok 载荷字节和）。

输出 JSON：`{"frames":[…],"gaps":[…],"regions":[…],"summary":{…}}`，退出 0。输入文件不存在或不含魔数时退出 2，stderr 含 `magic`。全部帧损坏但有魔数时仍退出 0，`frames_ok=0`。
