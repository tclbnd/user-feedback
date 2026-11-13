# 快照分页采集

Python 3.9+。运行 `python3 collector.py store collect demo.json` 和 `python3 collector.py store inspect`。source.py 是本地 REST 接口模拟器，使用独立的持久状态模拟重启后服务器继续前进，无需绑定端口。latest() 得到当前 token。fetch(token,cursor,if_none_match) 返回状态码与 JSON 对象。200 含 items、etag、next，next=null 标志结束。304 只可使用本代次同游标的已验证载荷。429 可按 retry_after 重试，410 表示该 token 过期，必须丢弃旧暂存后从 latest 的起点重新抓。404 与游标环报错。模拟器代码可保持原样。

记录含 id（字符串）、sort（整数）、value（字符串），各页按 (sort,id) 排序。允许重复页、跨页重复行和空中间页。一个 token 中同 id 内容相同，重复只保留一份。检查同一 token 不能混入不同版本。输出 rows 按 id 排序。collect --max-requests N 限制本次 fetch 次数（包含重试）。预算耗尽、暂存或故障时公开视图保持原版本。完整 token 发布后再重复采集同一个 token，不新增版本。

公开状态为 {version,token,rows,diff}，首个已发布版本为 1，初始状态为 0。diff 的 insert/update/delete 均为相对前一完整版本的主键字符串数组，升序。inspect --version N 读取历史。collect 可以额外返回进度与重试诊断。源模拟器只在 410 后更新当前 token。为了模拟新一次全量采集，可换 spec 文件并用相同 store；新 spec 可将同一 current 索引指向新 token。

传 --crash after-stage 或 before-cursor 时，在任一首次成功页面的载荷持久化后、消费位置公开前退出 97，恢复后不得丢载荷，允许条件读取时收到 304。before-publish 在最后一页持久化、完整版本可见之前退出 97。恢复后旧公开视图始终完整。各崩溃选项不依赖进程清理。

## collector.py 的实现契约

存储布局（所有写入均经临时文件 + os.replace 原子落盘）：

- `published.json`：当前公开视图 `{version,token,rows,diff}`，只在完整快照校验通过后整体替换；读者任何时刻读到的都是完整版本。
- `history/vN.json`：每个已发布版本的不可变历史，`inspect --version N` 直接读取；`--version 0` 返回初始空状态。
- `meta.json`：单调的采集代次序号 `next_gen`。
- `gen-<seq>/`：一个代次的全部临时状态。`gen.json` 绑定 token、当前游标、已见游标环、按 id 的行索引、重试诊断与计数；`page-NNN.json` 是逐页验证过的原始载荷（含 etag）。代次在 410 或发布后原子移入 `archive/`，永不跨 token 复用。

关键不变量：

1. token 与游标绑定：代次只服务一个 token；恢复时若暂存页的 token/游标与代次不符直接契约错误（退出 2）。游标环（已见游标重复）与 404 同样拒绝。410 记录 `token_expired` 后整代次归档，按 `latest()` 的新 token 从首页新建代次。
2. 先持久化载荷、再公开消费位置：200 页面先写 `page-NNN.json`，然后才更新内存与 `gen.json` 的游标。`after-stage`/`before-cursor` 崩溃后，恢复用该页 etag 发条件读取；304 只接受同代次同游标的已验证载荷，并幂等重建行索引。429 按 `retry_after` 等待重试，429/410 都计入 `--max-requests` 预算，预算耗尽返回 `incomplete`，公开视图保持旧版本。
3. 复合排序键 `(sort,id)` 逐页校验非降序；同 token 内同 id 内容必须一致，重复页（同 etag）与跨页重复行去重并计数，内容冲突按契约错误处理。
4. 发布前从磁盘重读全部暂存页，重建页链（cursor 衔接、next=null 收尾、无混合 token、行索引一致），通过后先写 history 再原子换 `published.json`。同一 token 重复采集返回 `already_published`，不新增版本。
5. `collect` 额外输出 `diagnostics`：代次号、本次请求数、页数、去重后行数、304 复用次数、重复页/重复行数，以及 `retries`（含 code、reason=`rate_limited|token_expired`、cursor、请求序号）。

## 验收测试

`python3 test_acceptance.py` 以子进程方式驱动 CLI，并内置一个直接读 fixture 的独立快照遍历器作为对照，覆盖：第 2 页后的排序键修改/插入/删除、空中间页、重复页与跨页重复行（逐键比对 rows，insert/update/delete 清单精确相等）；三种崩溃点的 97 退出与恢复（含 ETag 304 复用、读者始终只见上一份完整快照）；一次 410 token 过期（旧代次归档、新 token 完整发布、无过期数据泄漏）；预算耗尽的断点续跑；游标环/404/排序违规/同 id 异内容的契约拒绝。fixture 规格生成在 `specs/`，临时存储在 `test-stores/`（均不入库）。
