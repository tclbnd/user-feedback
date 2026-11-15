# 对账快照的读己之写与长事务下版本回收

`python3 mvcc.py input.json output.json`

input.json: `{"ops":[{"tx":1,"op":"begin"},{"tx":1,"op":"write","k":"a","v":1},{"tx":2,"op":"begin"},{"tx":2,"op":"read","k":"a"},{"tx":1,"op":"commit"},{"op":"gc"},{"tx":1,"op":"end"}...]}`

模型约定：
- begin 分配递增 ts;read 先查本事务 writes 再按快照可见性读(最新 commit_ts ≤ begin_ts)
- 未提交事务的写入对其他事务不可见
- gc 只回收对所有活跃快照不可见的旧版本(水位=最老活跃 begin_ts),不得回收快照仍可见的版本
- 输出 `{"reads":[{tx,k,v}],"versions":{k:[[ts,v]]},"gc_stats":{"reclaimed":n},"digest":"sha1"}`
- 事务交错顺序复跑终态与统计一致

缺陷基线:读总看最新已提交(脏读);gc 无视活跃快照全回收。
