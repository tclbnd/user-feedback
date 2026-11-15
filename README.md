# 批量回填的多分片完成屏障与超时收敛

`python3 barrier.py input.json output.json`

input.json: `{"shards":4,"timeout":30,"policy":"release|fail","batches":2,"events":[{"t":0,"shard":0,"batch":1,"type":"arrive|timeout|lost"}...]}`

模型约定：
- 计数-到达屏障：一批内所有分片 arrive 则 pass
- 某批次出现 timeout 事件时按 policy 放行(released)或整批失败(fail)，所有分片结论必须一致
- 失联(lost)分片不得让其余分片永久挂起；迟到 arrive 计入下一批不阻塞当前批
- 输出 `{"batches":[{"id":..,"result":"pass|fail|released|pending"}],"decisions":[{shard,batch,verdict}...],"digest":"sha1"}`
- 事件流乱序到达时决策摘要必须一致

缺陷基线：失联→pending 挂死；各分片按自身到达状态独立下结论 → 判定分裂；无超时处理。
