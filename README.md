# 隔行场序回放

Python 3.9+。执行 `python3 inverse.py demo.json output.json`。输入为 fields，每场 {id,parity,rows}，parity 为 top（原偶数扫描行）或 bottom（原奇数扫描行），rows 为 8 位亮度方阵的半高扫描行。cut=true 表示场前有已知硬切。每场时长 1001/60000 秒。允许局部场序切换。素材中每个 telecine 镜头把逐行源帧分成 top/bottom，以 2 或 3 场交替发送。3 场组的第 1、3 场完全相同。合成夹具中同源帧相邻扫描行亮度差较小，原生隔行相邻场具有明显运动。静帧可能让相邻源组内容相同，应按可解释的全镜头节奏匹配而非单纯画面去重。

在可信的 telecine 段输出逐行帧，由同一源帧的 top/bottom 按行交织，丢弃确证的重复场。结果包含 mode="progressive"、field_ids（所有用于该帧的输入场，包括判定为重复的场，按到达序）、sha256（逐行全亮度字节）、pts、duration（约分的 [分子,分母] 秒）。逐行时长 1001/24000 秒；局部时间轴以整数场时刻对齐，在切点累计误差 ≤ 一个场。原生隔行或不足以判定的段按原顺序输出，mode="interlaced"，field_ids 每对相邻场；单余场也保留并加入 diagnostics。隔行帧时长为场数×1001/60000，sha256 按实际存在的行输出。输出 segments 按相同 mode 的连续区间记载，至少给出 mode 和 fps（约分 [分子,分母]），单余场的时长单独报告。不得跨硬切交织。
