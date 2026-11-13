# Manifest 压实

Python 3.9+。CLI 形式为 `python3 lake.py ROOT ACTION`。ROOT 由应用初始化，数据文件按内容摘要不可变，SQLite 持有 manifest、当前版本和 pin。使用 append 文件写入 JSON 行对象列表（每行 id 全表唯一且不重用），delete 文件输入行 ID 列表。read 可选 --version，返回行与 manifest。state 返回 head、retained、pins、objects。

`prepare NAME` 固定当前版本及前两个数据文件，保存可恢复计划。同名调用返回同一个计划。准备只记录输入，不创建可见快照。`commit NAME` 发布一个新 manifest，用一份合并对象替换选中的两个文件。行 ID 与数据保持，允许物理保留已标记删除的行，也允许在新对象中略过这些行。旧版本可查。计划基线之后发生追加而选中文件和其删除视图未变时应重新基线并保留新增对象。选中文件被换掉或期间新删除影响所选行时返回 `{"status":"conflict","version":当前版本}`，不得推进版本。未影响所选文件的删除可重基线。成功返回 status=committed 和 version。重复已成功计划返回原成功结果，不能再次提交。

`pin VERSION` / `unpin VERSION` 模拟读者租约。GC 保留当前、已 pin 和仍待提交的计划所依赖的基线版本（计划可在明确完成或冲突时关闭），可清理其余快照和未引用对象。所有 retained 的 manifest 都必须可读。GC 应与其他写入互斥，或者以等效的事务策略保证安全。写入数据文件不得让其他读者看到半成品。冲突、清理及恢复诊断可增加 JSON 字段。

`commit NAME --crash after-data` 在新对象落盘、manifest/计划结果可见前无清理退出 97。`--crash after-publish` 在 manifest/计划结果持久化后、给调用方返回前退出 97。`gc --crash before-unlink` 在删除任意不可达对象之前退出 97。每个点只在单次调用中触发，不带选项重新执行即恢复。
