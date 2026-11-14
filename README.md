# sitemap+变更信号重抓优先级
Python3.9+ stdlib. `python3 prio.py signals.json output.json`
signals.json: `{"pages":[{"url","imp":0-1,"lastmod":ts,"etag":"e1","hist":次/周}],"etag_now":{"u":"e2"},"budget":k,"horizon":7d}`
- 优先级 p = w1*imp + w2*新鲜度(lastmod 距今倒数) + w3*(etag变化?0.3:0) + w4*hist; 权重 w1=0.4,w2=0.2,w3=0.2,w4=0.2
- 缺 lastmod → 新鲜度按 0.5; etag_now 无记录 → 变化信号按 0
- budget 内按优先级取前 k; 新 URL(无 lastmod 且无 etag)走快速通道必抓(不占 budget 但记 fast_lane)
- 输出 `{"queue":[{"url","p","reason"}],"skipped":[url],"fast_lane":[url],"coverage":加权覆盖,"digest"}`
缺陷基线: 只按 lastmod 排序, 无快速通道, 无去重.
