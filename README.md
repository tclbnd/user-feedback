# 本地分片对象落地

Python 3.9+。运行 `python3 make_demo.py` 后用 `python3 land.py ROOT land demo.json --workers 3`。inspect 返回当前已发布代次，inspect --generation NAME 可读取旧代次。generation 和对象 id 是安全单段路径。输入 manifest 包含 generation 及 objects。每个对象含 id、version、length、sha256（整对象）与 chunks。chunk 含 version、start、end（字节半开范围）、sha256（该范围）和 payload（Base64）。模拟短读或内容错误时，载荷可能不匹配长度或摘要。可选 retry（第二次尝试的 Base64），每个无效载荷至多重试一次。范围声明和版本不合法不可重试。片可乱序或重叠，重叠内容必须相等，未覆盖位置为缺片。冲突或缺片的对象隔离。

至少输出 generation、published、quarantine，按对象 id 排序。published 项含 id、length、sha256；ROOT/generation/id 保存已验证文件。quarantine 项含 id、reason。同一代次重复 land 保持相同结果，不能用不同 manifest 覆盖已发布代次。每个对象可记录范围、重试和隔离原因。始终先完成对象校验再原子发布代次指针，旧版本文件可读，未公开代次不能通过 inspect 读取。尚无已公开代次时返回 generation=null 及两个空列表。

持久化每个已验证范围。after-piece 在首个分片落盘后、范围登记之前退出 97。after-range 在首个范围登记完成后退出 97。before-pointer 在本代数据准备好、公开指针写入之前退出 97。带同一 manifest 重启可校验并复用可确认的片，统计 fetched_ranges（本轮获取的有效范围数）与 reused_ranges（本轮复用的范围数）。暂存清理不得破坏已发布代次。--workers 控制本地并发量。

## 实现说明（land.py）

磁盘布局：

- `ROOT/current.json`：公开指针，同目录临时文件 + rename 原子写入；含 `generation/published/quarantine/generations`，`generations` 记录所有已发布代次。
- `ROOT/<generation>/<id>`：已发布且通过全对象长度与 sha256 校验的对象文件；旧代次目录不被覆盖，`inspect --generation` 直接读取。
- `ROOT/staging/<generation>/manifest.json`：本代清单快照，用于同代异清单检测与幂等重入。
- `ROOT/staging/<generation>/status.json`：每对象状态（已登记范围键、失败片、重试次数、verified/quarantined 终态）。
- `ROOT/staging/<generation>/objects/<id>/pieces/<sha256>.part`：内容寻址的落盘暂存片，登记后立即删除；崩溃残留即“未确认片”。
- `ROOT/staging/<generation>/objects/<id>/ranges/<start>-<end>-<sha256>.range`：自包含的已验证范围注册表，是重启复用的唯一依据。
- `ROOT/staging/<generation>/result.json`：代次发布结果，先于公开指针落盘。

判定语义：片版本与对象版本不一致为 `version-conflict`；`start<0`、`end<=start`、`end>length` 为 `bad-range`（均不可重试）；载荷长度不符为 `short-read`，范围摘要不符为 `checksum-mismatch`，仅在带 `retry` 且该片此前未失败时重试一次；重叠区逐字节不同为 `overlap-conflict`；未覆盖位置为 `missing-range`；覆盖长度或整对象摘要不符分别为 `length-mismatch`/`digest-mismatch`。隔离对象持久化原因，禁止进入发布清单与发布目录。

重启流程：先扫描暂存区删除所有无对应范围登记的 `.part`（未确认片/孤儿片），再逐个复核 `.range` 文件名声明的边界与实际摘要，复核失败者删除并重读；通过者计入 `reused_ranges`。全部对象校验完成后才写对象文件、代次结果与公开指针，因此读者通过 inspect 只能观察到完整且摘要匹配的代次；未公开代次一律不可读。

## 验收

`python3 acceptance_test.py` 覆盖六组场景：20 对象的换代/错误内容/短读/错序/重叠/缺片/长度与摘要故障（逐字节与独立 sha256 核对，冲突对象不索引）、三个崩溃点的 97 退出与恢复计数核对、双代次原子发布与旧代可读、读者可见性、未确认片与孤儿片清理、清单与重复声明边界。
