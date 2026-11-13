# Manifest 压实

Python 3.9+。CLI 形式为 `python3 lake.py ROOT ACTION`。ROOT 由应用初始化，数据文件按内容摘要不可变，SQLite 持有 manifest、当前版本和 pin。使用 append 文件写入 JSON 行对象列表（每行 id 全表唯一且不重用），delete 文件输入行 ID 列表。read 可选 --version，返回行与 manifest。state 返回 head、retained、protected、collectable、pins、objects、pending_bases、conflicts。

## 快照与删除语义

manifest 为 `version -> {files:[对象], deleted:[行 ID]}`。某行在某快照可见，当且仅当它的 id 出现在该快照的某个对象中且不在 `deleted` 里。因此 `deleted` 是**快照级**删除视图，不是对象属性：同一对象可被旧 manifest（带删除标记）和新 manifest（不带标记）同时引用，两者的可见行集合各自成立。

压实读取固定基线快照选出两个对象，按提交时 head 的删除视图重写：被标记删除的行不再写入新对象，其 id 同时从新 manifest 的 `deleted` 中移除（该行在新 manifest 可达对象里已不存在）；未被选中的对象（含基线后追加的对象）上的删除标记保留。效果是新 manifest 与串行行级模型的可见行完全一致，而旧 manifest 仍描述旧的可见行集合。

`prepare NAME` 固定当前版本及前两个数据文件，保存可恢复计划。同名调用返回同一个计划，重复准备不改变基线。准备只记录输入，不创建可见快照。`commit NAME` 发布一个新 manifest，用一份合并对象替换选中的两个文件。行 ID 与数据保持，允许物理保留已标记删除的行，也允许在新对象中略过这些行。

## 提交校验与重基线

`commit` 在 `BEGIN IMMEDIATE` 事务内先按当前 head 校验，再决定发布或冲突：

- 选中对象必须仍在当前 head 的对象列表中；被另一次压实替换过则返回 `{"status":"conflict","version":当前版本,"reason":"selected-file-replaced"}`。
- 基线之后新增、且命中选中行的删除标记返回 `{"status":"conflict",...,"reason":"delete-affects-selection"}`（重写会与串行模型对该行是否存活产生分歧）。
- 选中对象缺失或内容摘要不匹配返回 `reason:"unreadable-input"`。
- 基线之后的追加，以及只命中未选中行的删除，均可重基线：新对象替换第一个选中对象的位置，其余对象顺序不变，追加对象与未受影响的删除标记都保留。

冲突不推进版本，计划就此关闭；冲突与原因写入 `conflicts` 表并由 `state.conflicts` 计数。成功返回 `{"status":"committed","version":N}` 并存为计划结果；重复提交已成功或已冲突的计划都返回原结果，不会再次发布。失败路径整事务回滚，新对象要么完整落盘要么不存在，不会发布半成品。

## GC、pin 与崩溃恢复

`pin VERSION` / `unpin VERSION` 模拟读者租约。GC 保留当前、已 pin 和仍待提交计划所依赖的基线版本（`protected`），可清理其余快照和未引用对象；所有 protected manifest 都可读。GC 在写锁内完成"校验引用 → 删除对象 → 删除快照"三步，与写入互斥，因此不会删掉并发的 commit 刚要引用的对象，也不会出现引用悬空。`commit --crash after-data` 在新对象落盘、manifest/计划结果可见前无清理退出 97；`--crash after-publish` 在 manifest/计划结果持久化后、给调用方返回前退出 97；`gc --crash before-unlink` 在删除任意不可达对象之前（确有可清理内容时）退出 97。每个点只在单次调用中触发，不带选项重新执行即恢复：after-data 的孤儿对象由 gc 记为未引用并清理，after-publish 的重复提交只回放计划结果。

## 验收

`python3 test_compaction.py` 枚举 20 组固定的"两个压实 × 追加/删除"交错，对串行行级模型比较每个已发布快照与每个 pin 的旧快照；随后在写新数据、发布指针、回收三类点分别注入崩溃并恢复，逐份校验保留 manifest 的引用完整性和当前快照的行 ID/删除状态。`python3 check_old.py` 用同一套交错跑修复前的实现，确认检出数据丢失/删除重现。
