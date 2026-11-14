# 直播 DVR 环形片段缓冲一致性
`python3 dvr.py req.json output.json`
req.json: `{"segs":200,"window":8,"crash_at":50,"readers":3,"speed":2}`
- 片段 seq 连续写入+清单原子更新: 先片段可读再添引用; 窗口滑动先确认新片段再移除旧引用
- crash_at 处模拟崩溃,重启从 state.json 恢复窗口(与未崩溃基线条目一致)
- 读者清单内片段全部可读; 3 读者见相同快照
- 输出 `{"manifest":[{seq,visible}],"dangling":n,"recovered":bool,"snapshots":{r:[segs]}}`

## 一致性顺序

每次 `append(seq)` 严格按下列顺序推进：

1. 片段写入 `segments/seg-XXXXXXXX.bin`：同目录临时文件 → `fsync` → `rename` → `fsync(dir)`。
   至此片段完整可读，且原子可见（外部读者不会看到半截文件）。
2. 提交状态 `state.json`：窗口条目、`next_seq`、`confirmed_seq`、代次一起写入，同样是
   `tmp + fsync + rename + fsync(dir)`。这是唯一的提交点，读者／恢复都以它为准。
3. 刷新物化清单 `manifest.json`（同一份内容）。
4. 才把被移除的旧片段登记为待回收，并延迟 `grace` 代、且无人持有快照时删除。

因此任何时刻崩溃：片段先于引用存在，绝无"清单里的片段读不到"。
`state.json` 与 `manifest.json` 都是整份替换，不存在半更新视图。

## 读者快照

`DvrBuffer.snapshot()` 返回不可变 `Snapshot`（条目元组 + 代次），并按条目对文件做引用计数。
窗口滑走后旧片段仍被 pin 住，读者读到的仍是它拿到的那一代；`read_seqs()` 逐条校验
长度、CRC32 与 seq 头，任何失效引用立即抛 `DanglingReference`。`release()`/`with` 释放后
才允许回收。未被 pin 的旧片段额外保留 `grace` 代（默认 `window`），兼顾直接按磁盘清单
读取的外部读者。

## 崩溃恢复

`open_recover()` 依次执行：

1. 清除所有 `*.tmp*` 片段/状态/清单残留（写入中断产物）。
2. 读取 `state.json`（校验 checksum）；损坏或缺失时，用磁盘上完整片段重新锚定窗口，
   避免误删已确认片段。
3. 校验已提交条目的长度/CRC/seq 头与 seq 连续性，损坏则回滚到最后一个有效条目，
   并把 `next_seq`/`confirmed_seq` 退回到该条目之后由写者重放。
4. 删除未被已提交状态引用的孤立片段。
5. 重建 `manifest.json`，写入 `RUNNING` 标记；`recovered` 反映是否发生过非干净停机/修复。

常驻 pid 标记 `RUNNING` 在 `close()` 时删除，用于判定上次是否干净退出。

## 崩溃点

`crash_at` 为触发崩溃的片段 seq（超出范围则忽略）。可用 `crash_phase` /
`crash_phases` 指定时刻，别名见 `_ALIASES`：

| 时刻 | 含义 | 磁盘清单 | 恢复后 `next_seq` |
| --- | --- | --- | --- |
| `after_segment` | 片段刚落盘，尚未提交 | 旧代（完整） | `crash_at`（片段由写者重放） |
| `before_manifest` | 同上，提交前一刻 | 旧代（完整） | `crash_at` |
| `after_state` | 状态已提交，清单尚未刷新 | 旧代（完整） | `crash_at + 1`（条目已确认） |
| `after_manifest` | 状态与清单都已更新 | 新代（完整） | `crash_at + 1` |

默认（不指定 phase）会依次跑完上述全部时刻，并要求每一种的最终窗口条目与无崩溃基线
逐项相同；任一不一致直接抛 `ConsistencyError`。崩溃时还会留下一个半截 `*.tmp` 片段，
用于验证"部分文件残留"被正确忽略与清理。

## 可选请求字段

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `state_dir` | `./.dvr_state` | 持久化目录（`state.json`、`manifest.json`、`segments/`） |
| `grace` | `window` | 旧片段在离开窗口后再保留的代数 |
| `resume` | `false` | 直接从 `state_dir` 恢复后续写，而非清空重来 |
| `crash_phase` / `crash_phases` | `PHASES` | 崩溃时刻，单值或列表 |
| `hard_crash` | `false` | 用子进程 `os._exit` 制造真实进程死亡（父进程随后恢复） |
| `stress` | `true` | 是否执行 2 倍速 200 片段边界压力检查 |
| `stress_delay` | `0.0005` | 每次 append 的基础间隔（秒，再除以 `speed`） |
| `segment_delay` | `0` | 主写入路径的每次 append 间隔 |

## 验证

`run()` 在返回前自检：恢复窗口 == 无崩溃基线；2 倍速滑动 200 个片段时多读者在边界
无一失效引用，且至少有一代被全部读者观测到且内容相同；终态 `readers` 个并发读者
（`threading.Barrier` 同步起跑）快照完全一致；磁盘 `manifest.json` 的序列等于已提交
窗口且逐条可读（`dangling == 0`）。

```bash
python3 -m unittest discover -s tests -v
```

覆盖：三个（实为四个）崩溃点与基线逐项比对、崩溃瞬间磁盘清单无失效引用、半截片段/
半截状态/半截清单残留、state.json 损坏或丢失、已提交片段损坏回滚、真实子进程
`os._exit` 崩溃、干净停机后 resume、2 倍速 200 片段边界压力、快照 pin 与延迟回收。

## 缺陷基线

原实现清单先写、片段后写（`visible` 靠 `seq % 3` 模拟），崩溃时只丢弃内存窗口、
磁盘无状态；读者会在窗口边界拿到未落盘的引用，重启窗口与基线不一致。

## 兼容性说明

- `manifest` 只列已提交窗口条目且 `visible` 恒为 `true`：`visible` 现在表示"引用可读"，
  由 CRC/长度/seq 头校验得出，而不是片段"是否就绪"的模拟标记。
  未提交（尚未落盘或写入中断）的片段不会出现在清单里，因此 `dangling` 恒为 0。
- 相对基线新增请求字段均为可选（见上表），默认行为即满足验收；
  输出仍是 `{manifest,dangling,recovered,snapshots}` 四个键，`snapshots` 值为 seq 列表。
- 默认 `state_dir` 为 `./.dvr_state`，已被 `.gitignore` 忽略；压力检查在独立临时目录
  中执行，不污染持久化目录。

