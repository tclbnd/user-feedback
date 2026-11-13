# 快照与变更日志复制

Python 3.9+。试用 `python3 replicate.py apply --source demo.json --db replica.db`，再用 `python3 replicate.py inspect --db replica.db` 查看结果。db 可以改用 SQLite。每个测试从不存在的 db 开始。

源的 `snapshot_id` 标识稳定快照，`snapshot_lsn` 是其包含的最后位点，`available_from` 是源保留的最低可恢复位点。已有 db 若换了 snapshot_id，或可恢复位点低于 available_from，应保留已发布结果并退出 2，stderr 含 `resnapshot_required`。此夹具只会在首次使用时或已完整发布后提高 available_from。源完整事件前缀不可变，可在末尾追加。

`stage` 事件给出 `chunk` 下标。每块带 table 和键值字典 rows。seal 到达前不可见，且应已读完所有 chunk。seal 发布基线并应用已缓冲的可提交事务。日志事件包括 begin、op、commit、abort、disconnect 和 reconnect。事务事件带 xid，commit 带严格递增的整数 lsn（同事务完整重放可重复使用旧位点）。op 为 set、delete 或 move，move 带 new_key 和可选 patch。begin 与 commit/abort 可跨 stage、进程和断流；未提交事务始终不可见。忽略低于或等于基线位点的事务。同 xid 和 lsn 的完整重复事务不重复应用。

`--limit N` 处理到完整事件前缀的第 N 条（累计，非本轮数量）。输出 JSON 至少包含 published、tables、audit、visible_lsn 和 applied_events。发布前 tables 和 audit 均为空，visible_lsn 为 null。audit 按实际应用的事务排序，每项包含 xid 和 lsn，建议附按表变化统计。每条事务和它的可见位置必须一起持久化。

传 `--crash` 可在匹配的首个阶段无清理退出 97。after-stage 表示持久化一批快照后，before-publish 表示基线公开前，before-commit 表示已发布状态下应用一条事务前，after-commit 表示提交刚持久化后。后两者用于 seal 之后的提交。正常重启时不传该选项。


## 在线重建和代次切换

新增 `apply --replace`。第一次使用它时若已有已发布结果，允许为不同 snapshot_id 启动后台重建。新快照的 snapshot_lsn 必须高于已发布位点，且每个库最多保留一个尚未发布的重建代次。重建独立保存 chunk、事务、已读事件数及日志保留边界。使用 `--replace` 和相同 source 多次调用，按前缀继续重建。正在重建时，不带该参数的旧代次 apply 仍可应用旧日志；inspect 和旧代次 apply 返回旧代次的可见结果。

新的 seal 到达且完整时，在一次持久化提交中切换可见快照，并应用已缓冲的可提交事务。只发布在 seal 时 snapshot_lsn 仍高于旧可见位点的新快照。该条件失败时退出 2，stderr 含 stale_snapshot，旧快照和旧消费进度保留。切换后，旧 source（无论是否传 --replace）退出 2 且不得回滚。当前代次重放（无论是否传 --replace）使用原消费进度。事务 ID 只在各自快照代次去重。重复或失败不得清空可读代次。不同进程同时调用时，写者的检查点与提交必须一致，inspect 只能观察完整旧代次或新代次的完整事务前缀。

inspect 至少返回既有字段和 snapshot_id，值为当前已发布快照 ID，未发布时为 null。后台新代次未发布时，`--replace` 的标准输出也使用当前已发布视图。允许附加重建诊断字段。

在重建中沿用 after-stage、before-publish、before-commit、after-commit。另加 after-publish，在切换事务持久化完成且尚未消费 seal 之后的事件或给调用方返回前无清理退出 97。重启时从原有检查点继续。
