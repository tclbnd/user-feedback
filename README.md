# 分片落地冗余条带与降级读取一致性

`python3 stripe.py input.json output.json`

input.json: `{"k":4,"m":1,"blocks":["AAAA","BBBB","CCCC","DDDD"],"ops":[{"op":"fail","disk":2},{"op":"read"},{"op":"rebuild"},{"op":"read"}...]}`

模型约定：
- k 个数据块 + m 个校验块组成条带；m=1 单奇偶可容忍 1 块失联，m≥2 多校验布局须容忍 m 块失联
- 可恢复范围内降级读取在线重建，返回字节与原始一致
- 超出校验能力（失联数>m）必须明确报错（errors 记录，reads ok=false），不得返回损坏数据、不得误写
- 重建(rebuild)写回后校验仍一致；输出 `{"reads":[{ok,data,degraded}],"errors":[..],"stats":{degraded_reads,rebuilds},"digest":"sha1"}`
- 失联序列可重放，统计摘要一致

缺陷基线：只实现单 XOR；双块失联静默返回损坏数据；重建不写回校验。
