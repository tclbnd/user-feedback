# 版本化页面采集图谱的增量审计

Python 3.9+，标准库。运行 `python3 -B audit.py examples/smoke.json output.json`。
样例有入口 index、正文页 a、孤儿 lone。index 到 a 的导航链接应出现在两个方向的索引中，审计应报 lone。

## 输入

- `roots` 为入口 ID 列表。`pages` 是 ID 到页面的映射，每页有 `nav`、`body`、`res`、`anchors` 四个字符串列表。不存在的列表视为空。标识区分大小写，链接中的 `#` 后面是片段标识；单独 `#片段` 指向当前页。
- `resources` 将资源 ID 映射到 HTTP 状态，缺失或状态不在 200–399 为失效。`redirects` 是页面旧 ID 到目标页面 ID 的映射。先沿重定向链找目标，再检查片段。重定向目标没有片段。遇到重定向环或缺少终点，记录故障引用。
- `batches` 按提供顺序到达。每批有正整数 `version` 及 `changes`。每次修改含 `kind, key, value`。kind 为 `page/resource/redirect/roots`。页面修改替换完整页面，value 为 null 表示删除。入口整体替换使用 kind 和 key 均为 `roots`，value 为 ID 列表。
- 对同批相同实体的相同修订去重。若同批实体有两种不同值，整批拒绝。已见版本的同内容重送记 `replayed`，同版本异内容记 `conflict`。未见的旧版本记 `stale`。冲突、重放和晚到不改变已生效的图。

## 输出

- `index.outbound`、`index.inbound`、`index.resource_users` 是最终正向、反向和资源索引。每项为 `key: [[kind, neighbor], ...]`，kind 为 `nav/body/res`；出边中的资源也保留，包括坏资源。坏锚点仍保留到终点页的边。只输出非空邻接表，排序并去重。
- `issues` 为当前问题 ID 的有序清单。`orphan:<page>` 表示非入口、不可达且无入边的页面；`cycle:<id1>,<id2>...` 列出不可达循环分量，节点名排序。自环也算循环。引用故障为 `bad_anchor:<source>:<raw-link>`、`dead_target:<source>:<raw-link>`、`redirect_cycle:<source>:<raw-link>` 或 `dead_res:<source>:<resource>`。
- `paths` 给出每条可达引用故障从任一入口到引用方的最短 ID 路径。同长度时比较整条路径，取字典序最小。孤儿的定位为 `[page]`，循环的定位为分量里从最小 ID 出发的一条最短闭合环，平局按字典序。其他不可达引用的定位为 `[source]`。
- `batches` 逐批输出 `version, status, added, removed, affected, edges_visited`。status 另有 `applied`。新增和解除都是有序问题 ID；未应用的批次为空。affected 至少包括改动页和引用结果发生变化的页面。全图回算时给出额外的 `rebuild_reason`。
- `digest` 是最终原始页面、资源、重定向和入口的规范摘要。每个实体按 `[kind, key, value]` 转成键排序的紧凑 JSON，求 SHA-256 并按位异或，最后输出 64 位小写十六进制。kind 为 `page/resource/redirect/roots`，入口是键 `roots` 的单个实体。页面四个数组以及根列表先排序去重。这样单实体更新可撤回旧贡献再增加新贡献。

## 独立复验接口

保留 `audit.run(site)` 和 `edge_store.EdgeStore`。初始索引完成后，每次开始处理批次时调用 `begin_batch(version)`。图分析从 `outgoing(page)`、`incoming(page)`、`resource_users(resource)` 获取搜索用邻接。若需要做全图重算，也从这些入口读边。验收会在这三个调用处计数，包括重访，排除初始索引构建与最终序列化，逐个局部批次与 5% 的访问上限比较。同批 `changes` 的数组可按不同大小读取后合并，组内反向和重复事件都不会改变业务结果。

起始版只读取初始导航，能输出 JSON 报告。资源、正文、反向引用、变更批次和增量更新仍需完成。
