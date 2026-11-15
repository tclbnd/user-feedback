# 入仓去重的计数布隆与假阳性监控校准

`python3 bloom.py input.json output.json`

input.json: `{"buckets":4096,"k":4,"seed":1,"ops":[{"op":"ins|del|query","k":"key5"}...]}`

模型约定：
- 计数桶+ k 个独立哈希(种子化)；del 只在键确认存在时执行，防计数下溢污染
- 在线估计假阳性率(按插入量与桶占用)，接近饱和(>85%)时 warn=true
- 输出 `{"verdicts":[每query bool],"fp_estimate":float,"warn":bool,"counts":{...},"digest":"sha1"}`
- 操作序重放终态摘要一致

缺陷基线：del 盲目递减共享桶→假阴性；无 FP 估计与预警。
