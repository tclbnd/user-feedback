# 快照分页采集

Python 3.9+。运行 `python3 collector.py store collect demo.json` 和 `python3 collector.py store inspect`。source.py 是本地 REST 接口模拟器，使用独立的持久状态模拟重启后服务器继续前进，无需绑定端口。latest() 得到当前 token。fetch(token,cursor,if_none_match) 返回状态码与 JSON 对象。200 含 items、etag、next，next=null 标志结束。304 只可使用本代次同游标的已验证载荷。429 可按 retry_after 重试，410 表示该 token 过期，必须丢弃旧暂存后从 latest 的起点重新抓。404 与游标环报错。模拟器代码可保持原样。

记录含 id（字符串）、sort（整数）、value（字符串），各页按 (sort,id) 排序。允许重复页、跨页重复行和空中间页。一个 token 中同 id 内容相同，重复只保留一份。检查同一 token 不能混入不同版本。输出 rows 按 id 排序。collect --max-requests N 限制本次 fetch 次数（包含重试）。预算耗尽、暂存或故障时公开视图保持原版本。完整 token 发布后再重复采集同一个 token，不新增版本。

公开状态为 {version,token,rows,diff}，首个已发布版本为 1，初始状态为 0。diff 的 insert/update/delete 均为相对前一完整版本的主键字符串数组，升序。inspect --version N 读取历史。collect 可以额外返回进度与重试诊断。源模拟器只在 410 后更新当前 token。为了模拟新一次全量采集，可换 spec 文件并用相同 store；新 spec 可将同一 current 索引指向新 token。

传 --crash after-stage 或 before-cursor 时，在任一首次成功页面的载荷持久化后、消费位置公开前退出 97，恢复后不得丢载荷，允许条件读取时收到 304。before-publish 在最后一页持久化、完整版本可见之前退出 97。恢复后旧公开视图始终完整。各崩溃选项不依赖进程清理。

## 采集器实现（collector.py）

游标与快照 token 在整个采集代次内绑定：所有 fetch 都使用代次 meta 中保存的 token，410 或恢复时发现 token 已切换都会丢弃旧暂存并从 latest 的起点新建代次（gen-counter.json 记录单调递增的代次号）。每页先落 page-<cursor>.json，再更新 rows.json，最后才原子推进 meta.json 的消费游标，因此任意时刻崩溃都不会丢已抓载荷，也不会公开未消费的位置。

- 环检测：已消费游标集合 seen_cursors 拒绝重访；next 指回当前页或已消费页同样报错（exit 2），暂存保留以便排查，公开视图不变。
- 重复页/重复行：不同游标返回相同 etag 计为重复页；同 id 重复出现且内容一致时去重并计数，内容不一致即判定同一 token 混入不同版本并报错。空中间页正常推进。304 恢复的是未消费页，不计为重复页。
- 条件读取：恢复时对已持久化页携带 If-None-Match；304 仅在本代次确有同游标已验证载荷时合法，否则报错。
- 429：按 retry_after 记录原因后重试，请求计入 --max-requests 预算；预算耗尽返回 status=incomplete 与 progress，公开视图保持旧版本，下次调用从游标继续。
- 发布：所有页抓完后由持久化页重算快照并与 rows.json 交叉校验，通过后先写 history/vXXXX.json，再原子替换 published.json；同一 token 完整重采不新增版本。inspect 始终只暴露 {version,token,rows,diff}，初始为 version 0。
- 诊断：diagnostics.json 持久化事件流（generation-started、retry、token-expired、conditional-read、crash、published、generation-discarded 等）、按代次统计的 duplicate_rows/duplicate_pages 以及 429/410/404/304 重试次数。

store 目录布局：

```
store/
  published.json          # 当前公开完整快照（原子替换）
  history/v0001.json ...  # 每个已发布版本的完整复查记录
  gen-counter.json        # 采集代次计数器
  source-state.json       # 模拟器自身的持久状态（hits/current）
  diagnostics.json        # 跨重启的事件流与重试/重复诊断
  staging/                # 仅在采集进行中存在，发布或过期后清除
    meta.json             # 绑定 token、消费游标、seen_cursors/seen_etags、done
    crash.armed           # 崩溃点布防标记，恢复后自动解除
    gen-NNNN/page-<cursor>.json, rows.json
```

## 场景测试

`python3 scenario_test.py` 运行端到端测试（全部产物在 test-tmp/，已加入 .gitignore）。参考快照由测试直接遍历 spec 分页图独立计算，再与采集器发布结果逐键比较，并精确比较 delete 清单。覆盖：第 2 页后改排序键/插入/删除、空页与重复页；after-stage、before-cursor、before-publish 三类退出 97 与恢复（含 304）；一次 410 token 过期后弃旧代次、发布新 token 完整内容且读者持续可见旧版本；429 重试、预算耗尽恢复、游标环、404、同 token 版本混叠、空快照与历史版本复查。
