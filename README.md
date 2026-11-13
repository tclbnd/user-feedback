# 设备事件拆表

Python 3.9+，依赖 requirements.txt。运行 `python3 ingest.py ROOT ingest demo.ndjson`。每行对象有 id、rev（正整数）、schema（1 或 2）、device。schema 1 使用 readings=[{channel,value}]，schema 2 使用 samples=[{ch,v}]。输入数组可缺失、显式 null、空数组或非空，分别用 missing、null、empty、present 表示。channel 为非空字符串，值为整数。null 元素或无效子元素导致整事件拒收。源文件和行号用于拒收追踪。

同 id 只公开最高的有效 rev。新版本不合法时保留上一有效版。原样重放无变化。同 id/rev 的有效但不同载荷必须拒绝并保留原已接受事件。拒收记录幂等。公开父、子和拒收表是实际 Parquet，三个表的文件均位于每个已公开版本目录中（由 inspect 返回路径）。父 events 为 {id,rev,device,array_state} 按 id 排序。子 readings 为 {id,rev,ordinal,channel,value} 按 id,ordinal 排序。rejects 为 {id,rev,source,line,path,reason}，path 为类似 /samples/1/v 的指针。可添加列但不删既有列。

inspect 返回 {version,files:{events,readings,rejects}}，路径为 ROOT 相对路径；初始 version=0。inspect --version N 可读历史。首次 ingest 成功发布 version=1；接受有效变化或首次拒收记录才发布新版本。--crash-before-publish 在所有暂存列式表写完、公开版本前退出 97。状态与三个 Parquet 文件必须一起可见。

## 布局与实现要点

```
ROOT/
  versions/v0/{events,readings,rejects}.parquet   # 初始空快照（真实文件）
  versions/v1/{events,readings,rejects}.parquet   # 首个公开版本
  versions/vN/{events,readings,rejects}.parquet   # 每个公开版本自包含
  versions/vN/state.json                          # 私有：曾接受过的 (id,rev) 签名
```

- 每个公开版本目录自包含三张完整、互相引用的 Parquet 表，所以 `inspect --version N`
  读到的父/子表总是一致的；旧版本可查，且本版本内没有孤儿子行。
- 一次 ingest 先写入 `ROOT/.staging/vN-<uuid>/` 的全部表和状态，然后整目录
  `os.rename` 到 `ROOT/versions/vN/`，因此版本要么完整可见，要么完全不可见。
- `--crash-before-publish` 在暂存表全部写完后停止（退出 97），暂存目录从不公开；
  下次 ingest 先回滚该暂存目录，再从最后公开版本 + 本次输入重建快照，因此重放同一
  输入得到相同结果，回填的修正事件按正常规则被接受。
- `state.json` 记录历史上所有被接受的 `(id,rev)` 的规范化载荷签名，使"同 id/rev
  不同载荷必须拒绝"在版本被更高 rev 取代后仍然成立；若该文件缺失，则从公开表恢复
  当前可见事件的签名。
- 拒收行按 `(source,line,id,rev,path,reason)` 去重，因而是幂等的；只有新接受的
  事件或首次出现的拒收行才会触发新版本发布。

## 测试

`python3 -m unittest -v test_ingest`（需要 pyarrow）。测试用独立逐条解释器
`oracle.py` 重新计算父/子/拒收表并逐行比对，覆盖嵌套空数组、null 元素、空通道、
同 id 不同版本、多声道顺序、拒收契约、历史版本可读，以及"暂存后中断 → 回填两条
修正事件"的恢复流程（逐版本检查无孤儿子行、重复输入不产生额外明细）。

