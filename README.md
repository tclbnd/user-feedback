# 快照分页采集

Python 3.9+。运行 `python3 collector.py store collect demo.json` 和 `python3 collector.py store inspect`。source.py 是本地 REST 接口模拟器，使用独立的持久状态模拟重启后服务器继续前进，无需绑定端口。latest() 得到当前 token。fetch(token,cursor,if_none_match) 返回状态码与 JSON 对象。200 含 items、etag、next，next=null 标志结束。304 只可使用本代次同游标的已验证载荷。429 可按 retry_after 重试，410 表示该 token 过期，必须丢弃旧暂存后从 latest 的起点重新抓。404 与游标环报错。模拟器代码可保持原样。

记录含 id（字符串）、sort（整数）、value（字符串），各页按 (sort,id) 排序。允许重复页、跨页重复行和空中间页。一个 token 中同 id 内容相同，重复只保留一份。检查同一 token 不能混入不同版本。输出 rows 按 id 排序。collect --max-requests N 限制本次 fetch 次数（包含重试）。预算耗尽、暂存或故障时公开视图保持原版本。完整 token 发布后再重复采集同一个 token，不新增版本。

公开状态为 {version,token,rows,diff}，首个已发布版本为 1，初始状态为 0。diff 的 insert/update/delete 均为相对前一完整版本的主键字符串数组，升序。inspect --version N 读取历史。collect 可以额外返回进度与重试诊断。源模拟器只在 410 后更新当前 token。为了模拟新一次全量采集，可换 spec 文件并用相同 store；新 spec 可将同一 current 索引指向新 token。

传 --crash after-stage 或 before-cursor 时，在任一首次成功页面的载荷持久化后、消费位置公开前退出 97，恢复后不得丢载荷，允许条件读取时收到 304。before-publish 在最后一页持久化、完整版本可见之前退出 97。恢复后旧公开视图始终完整。各崩溃选项不依赖进程清理。
