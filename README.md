# 入仓去重的计数布隆与假阳性监控校准

`python3 bloom.py input.json output.json`

input.json: `{"buckets":4096,"k":4,"seed":1,"ops":[{"op":"ins|del|query","k":"key5"}...]}`

模型约定：
- 计数桶+ k 个独立哈希(种子化)；del 只在键确认存在时执行，防计数下溢污染
- 在线估计假阳性率(按插入量与桶占用)，接近饱和(>85%)时 warn=true
- 输出 `{"verdicts":[每query bool],"fp_estimate":float,"warn":bool,"counts":{...},"digest":"sha1"}`
- 操作序重放终态摘要一致

缺陷基线（已修复）：del 盲目递减共享桶→假阴性；无 FP 估计与预警。

修复要点（bloom.py）：
- del 仅在确认存在时执行；k 个哈希位置可能碰撞到同一桶，须按去重桶的
  多重性确认 `cnt >= 多重性` 才递减，否则拒绝且计数器完全不动 —— 防下溢污染
- 在线 FP 估计双通道：按插入量 `p_n=(1-e^(-kn/B))^k` 与按桶占用
  `p_occ=(非空桶/B)^k`；输出以占用校准的 `p_occ`（对 n 漂移鲁棒，
  饱和区实测误差 ~1%，而插入量估计已漂移到 0.22 vs 实测 0.13）
- 非空桶占用 > 85% 时 `warn=true`（粘性饱和预警）
- `counts` 含 `min_counter`（下溢监控）、`rejected_deletes` 等统计

验收（counting_bloom.py）：
- `python3 counting_bloom.py test`   — 6 项单元测试（含 3 万随机操作无下溢、
  重放/CLI 复跑摘要一致、FPR 估计校准）
- `python3 counting_bloom.py accept` — 2 组容量(B=1M 健康 / B=90k 饱和)
  × 2 种子 × 复跑：100k 混合负载 FN=0、在线估计误差 ≤20%、终态摘要一致
- `python3 counting_bloom.py run ... --log ops.jsonl` — 逐操作判定日志
