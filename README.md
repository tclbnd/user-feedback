# 本地站点图抓取

Python 3.9+。执行 `python3 mirror.py demo.json crawl.json --workers 6`。graph 包含 origin（带 scheme 的唯一授权源）、seeds、pages、disallow、spacing_ms。pages 键为规范化路径加查询字符串。相对 URL 按当前 URL 解析，去片段，折叠点段和连续斜杠。只保留白名单查询参数 id，按键和值排序。外源一律不得请求。disallow 为路径前缀，匹配后不得请求。种子可为相对或本源绝对 URL。无网络接口，所有请求按此脚本回放。

页面字段可为 body（UTF-8）、links、canonical、redirect、retry_after_ms。canonical 只允许指向本源、未禁止且存在的页面，跟随合法指向并记录 alias；redirect 替换请求目标。重定向和 canonical 环识别后停止环内别名解析。retry_after_ms 非空时，首次访问返回 429，第二次访问获取原页面。每次请求虚拟时间严格递增，间距至少 spacing_ms；429 后此 URL 的下一次请求要至少再等 retry_after_ms。--workers 表示最大并发 frontier 容量；虚拟时间可单调调度。--limit 为本次请求次数限制，重启时复用同一 state，保存未处理队列、虚拟时间和重试状态。

输出 {resources,aliases,edges,skipped,requests,done}。资源为按 url 排序的 {url,sha256}，哈希基于终端页面 body；alias 或 redirect 源不作为独立资源。aliases 映射已成功解析的源 URL 到最终 URL。edges 为已读正文页面的 [源,目标] 有向链接列表，按字典序去重，保留规范化后本源且允许的目标。requests 为 [{url,t,status}]，t 为整数虚拟毫秒。skipped 至少能区分禁止路径、外源和环。不同并发数下资源、别名和边集合必须一致。此项目只处理合成的站点图。
