# 入仓暂存日志的段管理与重放截断安全

`python3 wal.py input.json output.json`

input.json: `{"seg_size":8,"ops":[{"op":"append","batch":1,"records":[..]},{"op":"checkpoint"},{"op":"truncate"},{"op":"corrupt","seg":0,"pos":2},{"op":"ingest"},{"op":"crash"},{"op":"recover"}...]}`

模型约定：
- append 按序分配 seq 跨段连续;checkpoint 水位=当时最大 seq
- truncate 只能删整条记录 seq 全部 ≤ 检查点水位的段;段内尾部未过水位必须保留
- ingest 入库所有 seq≤水位 的记录;recover 崩溃后重放恢复到最近一致状态
- 损坏段恢复时跳过坏记录但保留其后合法记录(记录级定位)
- 输出 `{"ingested":[批id],"segments":[{"first":..,"last":..,"records":n}],"recovered":n,"digest":"sha1"}`;故障序列乱序复跑终态一致

缺陷基线:整段删除不看尾部水位;recover 遇坏记录放弃其后全部。
