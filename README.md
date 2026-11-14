# 分块下载断点续传与校验重组

Python3.9+ stdlib. `python3 dl.py req.json output [--concurrency N] [--chunk N] [--state PATH] [--max-retries N] [--reset] [--quiet]`

req.json（除 `source` 外均可选）:

```json
{"source":"src.bin","chunk":1024,"concurrency":4,"state":"state.json",
 "fail_at":[0.5,0.8],"corrupt_chunks":[3]}
```

## 契约

- 源文件在 req 同目录；按 `chunk` 分块、编号从 0；`state` 持久化**已完成的块 + 各自
  sha256 + 精确 offset/length**（原子写）。
- **断流**：进度达到 `fail_at` 各点（≤1 视为比例，>1 视为绝对字节）时抛异常退出（码 3）。
  续跑**只对缺失块发精确 `[offset, offset+length)` 的 Range**；已完成块按 sha 复核
  `.part` 后复用，绝不盲信 state（state 被篡改/截断时按可信清单从 `.part` 重建）。
- **块级校验**：每次响应都校验「起点对齐 + 长度 + 块 sha」。错位/短包/摘要不符 → 该块
  的坏数据写入 `<output>.quarantine/` 隔离，然后**只重取该块**。
- **整体校验**：重组后对整文件做 sha256；失败则按块定位坏块 → 隔离 → 重取 → 再校验，
  只有整体校验通过才把 `.part` 原子替换为最终输出。
- **Range 退化**：源不支持 Range（伴生 `src.bin.norange`）或只对头部 `range_head` 字节
  支持 Range 时，退化为整体重传，`mode="full"` 并在 `alerts` 中明确告警。
- **并发无关**：同一块映射下 1/4/8 并发只改变调度顺序，最终文件字节一致、块记录
  （`i`/`sha`/`retried`）一致。块边界与偏移全程 64 位整数。

## 输出

`output` 为最终文件（**逐字节等于源**），`<output>.meta.json` 为:

```json
{"mode":"chunked|full","status":"complete|interrupted|failed","exit_code":0,
 "source":{"size":0,"sha256":"…","range_mode":"full|head|none","range_head":0},
 "chunk":1024,"concurrency":4,
 "chunks":[{"i":0,"offset":0,"length":1024,"sha":"…","retried":false,"attempts":1,
            "status":"done","source":"range|state|full","reasons":[],"quarantined":0,
            "ranges":[[0,1023]]}],
 "stats":{"retries":0,"bytes":0,"blocks_done":0,"blocks_total":0,"quarantined":0,
          "requests":0,"served_bytes":0,"runs":1,"elapsed_s":0,"bytes_per_s":0,
          "run_requests":0,"run_served_bytes":0,"degraded":false},
 "throughput":{"bytes_per_s":0,"blocks_per_s":0,"elapsed_s":0,"concurrency":4,
               "interrupted":false,"run":{…}},
 "alerts":["…"],
 "log":["[0.001s] done block 0 [0,1023) sha …","…"]}
```

`stats.requests/served_bytes/runs` 是**跨续传轮次的累计值**，`stats.run_requests` 是本轮值。

退出码：`0` 完成且整体校验通过；`3` 模拟断流（可直接续跑）；`4` 校验/重试耗尽；
`2` 用法错误；`1` 其它错误。

## 测试

```bash
python3 tests/acceptance.py     # 18 个黑盒验收用例（断流/坏块/并发/退化/边界）
python3 tests/test_internal.py  # 白盒：整体校验失败的块级定位重取、64 位块映射
python3 tests/demo_acceptance.py ./demo-out   # 打印人类可读的逐块状态与吞吐
python3 tests/repeat.py 5       # 重复跑验收套件，检查并发偶发失败
```

故障注入参数（源端模拟器，都写在 req.json）：`fail_at`、`corrupt_chunks`、
`corrupt_times`（每个坏块投递几次坏数据，默认 1）、`flips`（每块翻转几个字节，默认 2）、
`misalign_chunks`（服务端回错位起点）、`range_head`（仅头部 Range）、`rate`/`latency_ms`
（节流以便观察吞吐）。

## 与缺陷基线的差异

基线：断流后从头重下、无块级校验、corrupt 不检测、Range 不支持时不退化。现在：
块级 state + sha 复核续传、64 位精确 Range、块级隔离重取、整体校验定案、
不支持 Range 时明确退化并告警、并发数不影响最终结果。

