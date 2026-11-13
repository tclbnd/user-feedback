# 合成实体删除与旧快照恢复

Python 3.9+。执行 `python3 erase.py ROOT apply demo.json`。ROOT 为本地目录。apply 读 events，事件含唯一 event、type、key（合成实体键）、epoch（实体代次）。customer 含 region，purchase 含 purchase、value（整数），delete 对应 key/epoch。purchase 只在同 key/epoch 有 customer 时可见，允许先到后配。delete 屏蔽同 key 的所有 epoch≤该值的数据，后到的旧事件也不应可见。新 epoch 可有新数据。完全重复 event 忽略，载荷冲突整批报错。同一类型和实体键下的 purchase ID 唯一。所有数据合成。

inspect 返回 version、customers（按 key,epoch 排序的 {key,epoch,region}）、purchases（按 key,epoch,purchase 排序的 {key,epoch,purchase,value}）、totals（按 region 排序的 {region,count,value}）。totals 由可见明细聚合。输出 proof 含 active_delete_count 和 sha256。sha256 对 [customers,purchases,totals] 的 canonical JSON UTF-8 字节计算，使用 sort_keys=True、separators=(",",":")、ensure_ascii=False。删除原始操作和暂存元数据可另行持久化，但不会作为业务行返回。

restore VERSION 从一个旧版本重建并应用当前所有删除门槛，然后原子公开为新版本。恢复所选旧快照的业务数据，其他未包含的后来数据可以不恢复；删除门槛不能倒退。--crash-before-publish 在 apply 或 restore 写新状态但公开前退出 97，重跑不得造成旧删除数据重新可见。提交按版本自增，从 1 开始；无有效新事件的 apply 可不增版本。初始为空。
