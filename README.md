# 入仓暂存日志的段管理与重放截断安全

`python3 wal.py [--stats] input.json output.json`

input.json: `{"seg_size":8,"ops":[{"op":"append","batch":1,"records":[..]},{"op":"checkpoint"},{"op":"truncate"},{"op":"corrupt","seg":0,"pos":2},{"op":"ingest"},{"op":"crash"},{"op":"recover"}...]}`

模型约定：
- append 按序分配 seq 跨段连续;checkpoint 水位=当时最大 seq
- truncate 只能删整条记录 seq 全部 ≤ 检查点水位的段;段内尾部未过水位必须保留
- ingest 入库所有 seq≤水位 的记录;recover 崩溃后重放恢复到最近一致状态
- 损坏段恢复时跳过坏记录但保留其后合法记录(记录级定位)
- 输出 `{"ingested":[批id],"segments":[{"first":..,"last":..,"records":n}],"recovered":n,"digest":"sha1"}`;故障序列乱序复跑终态一致

缺陷基线:整段删除不看尾部水位;recover 遇坏记录放弃其后全部。

## 实现要点

**截断判定(按检查点水位)。** 只有段内**全部**记录的 `seq ≤ checkpoint`
时整段才可回收;跨水位的尾段整段保留,不会因首条记录已提交而被删。
提交水位以下的记录即使所在段被回收,也已进入持久化入库账本(按 `seq`
去重),因此 `checkpoint → truncate → ingest` 与
`checkpoint → ingest → truncate` 结果一致,不会因回收顺序丢批次。

**崩溃恢复。** `crash` 只丢弃易失状态;`recover` 从日志重放到最后一次
一致的检查点(水位以上的尾部记录不参与重放),入库账本按 `seq` 幂等,
重复恢复不会重复计数。恢复点即 `recovery_point`(最近检查点水位)。

**记录级损坏定位。** `corrupt` 只标记单个记录(写入 `<BAD>` 哨兵),
段内偏移不变;重放逐条跳过坏记录,其前、其后的合法记录都保留。
除物理地址 `{"seg":i,"pos":j}`(支持 Python 负索引)外,还可逻辑定位:
`{"batch":b,"index":k}` 或 `{"seq":s}`——这样不同 `seg_size` 的故障注入
命中的是同一条逻辑记录,便于复跑比对。

**并发安全。** append/truncate/ingest/recover 共用一把 `RLock`;truncate
在新列表上过滤后一次性原子替换段列表,追加方不会写入已被回收的段。

**输出与统计。** 默认输出上面四个字段。加 `--stats`(或 input 里
`"include_stats":true`)额外输出 `recovery_point` 与 `lifecycle`
(`segments_created/truncated/live`、`records_appended/ingested/truncated/
corrupted`、`checkpoints/crashes/recoveries`、`max_truncated_seq`),
其中 `max_truncated_seq ≤ checkpoint` 是截断水位的验收不变量。

## 运行与验收

```
python3 wal.py input.json output.json        # 单次执行
python3 acceptance.py                        # 端到端验收场景(20 次崩溃等)
python3 -m unittest -v test_wal              # 18 个单元/回归测试
```

`acceptance.py` 覆盖:20 次注入崩溃后已提交批次零丢失且
`max_truncated_seq ≤ checkpoint`;损坏段恢复保留前后合法记录;
2 组段大小 × 3 种乱序故障的入库终态与恢复摘要一致。

