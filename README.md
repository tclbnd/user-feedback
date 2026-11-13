# 本地站点图抓取

Python 3.9+，无第三方依赖。执行 `python3 mirror.py demo.json crawl.json --workers 6`。graph 包含 origin（带 scheme 的唯一授权源）、seeds、pages、disallow、spacing_ms。pages 键为规范化路径加查询字符串。相对 URL 按当前 URL 的目录解析，去片段，折叠点段和连续斜杠。只保留白名单查询参数 id，按键和值排序。外源一律不得请求。disallow 为路径前缀，匹配后不得请求。种子可为相对或本源绝对 URL。无网络接口，所有请求按此脚本回放。

页面字段可为 body（UTF-8）、links、canonical、redirect、retry_after_ms。canonical 只允许指向本源、未禁止且存在的页面，跟随合法指向并记录 alias；redirect 替换请求目标。重定向和 canonical 环识别后停止环内别名解析。retry_after_ms 非空时，首次访问返回 429，第二次访问获取原页面。每次请求虚拟时间严格递增，间距至少 spacing_ms；429 后此 URL 的下一次请求要至少再等 retry_after_ms。--workers 表示最大并发 frontier 容量；虚拟时间可单调调度。--limit 为本次请求次数限制，重启时复用同一 state，保存未处理队列、虚拟时间和重试状态。

输出 {resources,aliases,edges,skipped,requests,done}。资源为按 url 排序的 {url,sha256}，哈希基于终端页面 body；alias 或 redirect 源不作为独立资源。aliases 映射已成功解析的源 URL 到最终 URL。edges 为已读正文页面的 [源,目标] 有向链接列表，按字典序去重，保留规范化后本源且允许的目标。requests 为 [{url,t,status}]，t 为整数虚拟毫秒。skipped 至少能区分禁止路径、外源和环。不同并发数下资源、别名和边集合必须一致。此项目只处理合成的站点图。

## 实现结构

- `mirror.py` — 爬虫本体。
  - `normalize_url` / `collapse_path` / `page_key`：同源限定、片段去除、点段与连续斜杠折叠、查询白名单（仅 id）与排序；相对引用按当前 URL 的目录解析（RFC 3986 语义）。
  - `Crawler.discover`：统一入口，外源记 `external`、命中 disallow 记 `disallow`，合规 URL 去重后进入按 host 划分的 FIFO frontier。
  - 虚拟时钟调度器：按 host 维护并发容量（`--workers`）、`spacing_ms` 速率门和 `Retry-After` 门；全局用“最早发现”规则在 host 间公平选择，时间戳严格单调递增。
  - 429 语义：首次访问节流页面返回 429 并把 URL 放回队首，写入该 URL 的最早可重试时刻；第二次访问返回正文。
  - canonical/redirect：合法指向记录来源与别名，环（自环与多跳环）冻结环内节点并记 `cycle`（区分 `redirect_cycle` / `canonical_cycle`），非法 canonical 按 `invalid_canonical` / `canonical_disallowed` / `canonical_missing` 跳过，重定向到 404 的整条链记 `redirect_missing` 而非别名。
  - 可恢复：每轮调度后把 frontier、虚拟时钟、Retry-After、已读页面、边、别名图与请求日志写入 state（`partial: true`）；重启时重排在途 URL 并继续，已完成的 state（`partial: false`）拒绝重复运行。
- `sitegen.py` — 生成 70 个页面、含四类环（链接环、重定向自环、重定向双节点环、canonical 环/自 canonical）的确定性夹具 `run/graph70.json`，并覆盖查询白名单、规范化、非法 canonical、外源、disallow 与 429。
- `reference.py` — 独立真值遍历（基于 `urllib.parse` 的另一套规范化与 BFS），输出资源、边、canonical 分组和跳过类别，用于与爬虫结果交叉校验。
- `test_mirror.py` — 28 条验收测试：`python3 -m unittest -v test_mirror`。

## 验收要点对应

1. 70 页四类环夹具在 `--workers 1` 与 `--workers 6` 下的资源、边、canonical/别名分组一致，并与 `reference.py` 的独立图遍历逐项相等（哈希、别名、边、跳过原因）。
2. 虚拟时钟注入 429、`--limit` 模拟宕机重启、等待队列恢复：逐请求断言禁止路径与外源零访问、相邻请求时间差不低于 spacing_ms、429 后重试不早于 retry_after_ms；分块重启（含在 429 边界重启、更换 workers、手工构造在途崩溃 state）后的最终索引与请求日志和不间断抓取完全一致。

## 输出字段补充

`skipped` 条目形如 `{url, reason, source? , target? , rule? , kind?}`，reason 取值：`external`、`disallow`、`missing`、`invalid_redirect`、`redirect_disallowed`、`redirect_missing`、`invalid_canonical`、`canonical_disallowed`、`canonical_missing`、`cycle`。state 文件在未完成时额外携带 `partial: true` 与调度快照，完成后为 `partial: false` 的最终索引。

