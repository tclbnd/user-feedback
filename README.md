# 可回放实体关系

Python 3.9+。用 `python3 entities.py apply state.json demo.json`，再用 `python3 entities.py inspect state.json --version 3`。输入 nodes 为稳定、唯一的字符串节点。links 按数组顺序给出，含 event（全局唯一事件标识）、a、b、source、kind（same 或 conflict）、action（set 或 retract）。边键为无向端点、source 和 kind。重复 event 且载荷等价时忽略，有冲突则退出 2 并保留状态。撤回只移除指定来源。不同来源的活动边并存。apply --upto N 表示消费累计 N 个输入位置；重跑、分块及完整重放结果一致。每个前缀是逻辑版本，初始版本 0。

分组从每个节点独立开始，按归一化 (a,b) 字典序扫描有任一活动 same 来源的边。只要连接两群后会把任一活动 conflict 对放在同一群，就拒绝该合并。输出活动冲突造成的 blocked_links，格式为 [[a,b],...]，按字典序。分组成员升序，群 ID 为 entity- 加最小成员。它由组内最小锚节点继承；合并到更小锚时，被归并的旧 ID 作为别名指向当前拥有该锚的群，分裂时恢复锚的独立 ID 并撤销相应别名。只有曾经出现为群 ID 的节点才可能成为别名。别名为 [{from,to},...] 按 from 排序。

inspect 或 apply 输出 {version,clusters,aliases,blocked_links,changes}，clusters 为 [{id,members},...] 按 id 排序。changes 记录每个有群归属变化的逻辑版本，形式为 {version,move:[{node,from,to},...]}，move 按 node 排序，初始版本无需记录。可增加活动边证据与受影响区域诊断。重算只须局限在新增或移除边涉及的支持图连通区域，并考虑冲突约束。inspect --version 读取任意已消费前缀。以仅影响独立小群的操作检查其他群 ID 不变。
