# 按位计数排序的并行前缀和与次序稳定性

`python3 radix.py input.json output.json`

input.json: `{"pairs":[[key32,value]...],"threads":4,"chunk":64,"seed":1}`

模型约定：
- 8 位桶 LSD 基数排序(4 遍),各遍之间相等键保持原始相对次序(稳定)
- 分块计数与全局前缀和解耦,threads/chunk 变化不改变最终排列
- 输出 `{"sorted":[[k,v]],"pass_stats":[{bucket_counts:[256]}*4],"stable_check":bool,"digest":"sha1"}`
- 同一输入乱序分块重放摘要一致

缺陷基线:按 (k,v) 元组排序——等键次序漂移,非基数实现。
