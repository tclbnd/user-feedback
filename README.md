# 乱序时序分层缓冲与窗口压实

Python 3.9+，标准库。`python3 tsdb.py request.json output.json`

request.json：

```json
{"watermark":30,"levels":[64,256],
 "points":[{"ts":100,"key":"a","v":1,"ver":1},...],
 "queries":[{"at":150,"key":"a"},{"at":200}]}
```

语义：

- `points` 按写入序到达（时间戳乱序）；`watermark`：ts 早于 `max_ts - watermark` 的点为「迟到超水位」，走补偿通道 `late`，不进入常规层。
- 分层缓冲：L0 容量 levels[0]，写满后**窗口内排序**压实进 L1（容量 levels[1]），L1 满压实进 L2（无界）。压实合并时同 (key,ts) 按 `ver` 大者胜（等 ver 保留后到者不得重复）。
- `queries`：`{"at":T,"key":k}` 查 key 在 [0,T] 内所有点按 ts 升序（同 ts 按 ver 降序）；无 key 查全部 key。结果必须与离线全排序一致；迟到点单独计入 `late` 不参与常规查询。

输出：

```json
{"level_stats":{"L0":n,"L1":n,"L2":n,"late":n,"compactions":n},
 "results":[{"at":T,"key":k,"points":[{"ts","key","v","ver"}...]}...],
 "digest":"sha256前16(对规范化层内容)"}
```

现有实现缺陷：不分层全堆一层（无水位判断，迟到点混入正常查询）、压实不去重（同键多版本并存）、查询顺序按写入序而非时间序。
