# sitemap+变更信号重抓优先级
Python3.9+ stdlib. `python3 prio.py signals.json output.json`
signals.json: `{"pages":[{"url","imp":0-1,"lastmod":ts,"etag":"e1","hist":次/周}],"etag_now":{"u":"e2"},"budget":k,"horizon":7d}`

## 优先级
`p = w1*imp + w2*fresh + w3*(etag变化?0.3:0) + w4*hist_sig`，权重 `w1=0.4,w2=0.2,w3=0.2,w4=0.2`，取值 [0,1]。

### 信号定义（缺失信号与过期信号）
- **sitemap lastmod —— 过期信号**
  - 缺失信号：无 `lastmod` -> `fresh=0.5`（中性值），决策标记 `missing_lastmod`
  - 过期信号：`age_days=(now-lastmod)/86400` 超过 `horizon` -> 标记 `stale_lastmod`
  - 新鲜度：`fresh = 1/(1 + min(age,30d)/7d)`（age=0→1.0，7d→0.5，30d 封顶）
- **ETag —— 内容变更信号**
  - 缺失信号：历史 `etag` 或 `etag_now` 任一缺失 -> 变化信号按 0，标记 `missing_etag`
  - 变化信号：两侧都在且不等 -> 0.3 并标记 `etag_changed`，相等 -> 0
- **hist（次/周）**：`hist_sig = min(1, hist*horizon/7)`，缺失按 0

## 入队
- 新发现 URL（无 `lastmod`、无历史 `etag`、`etag_now` 也无记录）走**快速通道**，不占 budget，记入 `fast_lane`，仍计算 p 并参与覆盖统计。
- 其余页面按 p 降序取前 `budget`；同 URL 多条记录先**确定性去重合并**（imp/hist 取最大、lastmod 取最新、etag 取最新 lastmod 记录携带值），合并次数记入 `merged_from`。
- 所有排序带 URL 字典序 tie-break，**信号输入顺序变化不影响队列与 digest**。

## 输出
`queue/skipped/fast_lane/coverage/digest`（原契约）+
- `distribution`：全量页面优先级分布 `{high:>=0.66, mid:>=0.33, low}`
- `decisions`：逐条入队决策（lane=queue/skipped/fast_lane、p、reason、合并次数、各信号标记）
- `coverage_stats`：预算使用、加权覆盖、分重要度（high>=0.70/mid>=0.40/low）覆盖率、队列内优先级分布
- `digest`：对 queue/fast_lane/skipped 内容的 sha256 前 16 位，重排输入保持不变

## 测试与模拟
- `python3 test_prio.py`：信号语义、独立参考实现逐条核对（200 随机页+双周期）、去重合并、重排不变性、快速通道与预算、双周期验收。
- `python3 simulator.py`：虚拟时钟两周期（60 页站点，注入 lastmod+ETag 变更、新发布 URL、重复记录），产出 `sim_report.json`，并断言高重要度变更页预算内全抓、未变页抓取相对全量基线下降 ≥60%、随机重排摘要一致。

缺陷基线: 只按 lastmod 排序, 无快速通道, 无去重.
