# 隔行场序回放

Python 3.9+。执行 `python3 inverse.py demo.json output.json`。输入为 fields，每场 {id,parity,rows}，parity 为 top（原偶数扫描行）或 bottom（原奇数扫描行），rows 为 8 位亮度方阵的半高扫描行。cut=true 表示场前有已知硬切。每场时长 1001/60000 秒。允许局部场序切换。素材中每个 telecine 镜头把逐行源帧分成 top/bottom，以 2 或 3 场交替发送。3 场组的第 1、3 场完全相同。合成夹具中同源帧相邻扫描行亮度差较小，原生隔行相邻场具有明显运动。静帧可能让相邻源组内容相同，应按可解释的全镜头节奏匹配而非单纯画面去重。

在可信的 telecine 段输出逐行帧，由同一源帧的 top/bottom 按行交织，丢弃确证的重复场。结果包含 mode="progressive"、field_ids（所有用于该帧的输入场，包括判定为重复的场，按到达序）、sha256（逐行全亮度字节）、pts、duration（约分的 [分子,分母] 秒）。逐行时长 1001/24000 秒；局部时间轴以整数场时刻对齐，在切点累计误差 ≤ 一个场。原生隔行或不足以判定的段按原顺序输出，mode="interlaced"，field_ids 每对相邻场；单余场也保留并加入 diagnostics。隔行帧时长为场数×1001/60000，sha256 按实际存在的行输出。输出 segments 按相同 mode 的连续区间记载，至少给出 mode 和 fps（约分 [分子,分母]），单余场的时长单独报告。不得跨硬切交织。

## 实现约定（inverse.py）

- 相邻场一致性：把 top/bottom 按行交织，用相邻扫描行平均差除以场内垂直活动度得到无量纲 ratio；ratio ≤ 4 视为同源可交织，否则按运动/切点处理。
- 局部周期/场序/相位：在每个 `cut` 划定的镜头内用动态规划选择 2 场或 3 场组，3 场组要求第 1、3 场奇偶性相同且行内容完全一致；2,3,2,3... 节奏只作为弱先验，用于打破静帧造成的等分组歧义，强证据始终优先。
- 无可信匹配：不做交织、不丢场，相邻场按到达序两两保留为 interlaced；奇数余场单独成帧并写入 `diagnostics.leftover_field`，时长 1001/60000 秒单独报告。
- 硬切：在每个 `cut`（以及高置信推断的漏标切点）处重置跨场匹配和节奏相位，任何组都不会跨越切点。
- 标记错误：奇偶性重复且交织证据相反时修复并输出 `parity_mismatch`；无法交织的重复奇偶性按场序切换处理；短/缺行场输出 `truncated_field` 且不参与交织。
- 有理时钟：全部时间用 `Fraction` 精确计算，pts 是约分 [分子,分母] 秒，每段镜头起始对齐到整数场时刻；`timeline.shots[*].drift_fields` 给出每个镜头相对场时钟的累计漂移，超过 1 个场周期时写 `timing_drift_exceeded`。
- 来源：每帧带 `sources`（场号、奇偶性、weave_top/weave_bottom/duplicate/native_field/leftover 角色）和 `dropped_duplicate_field_ids`；顶层 `provenance` 给出每个原始场到结果帧的映射。
