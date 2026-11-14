# B+树快照引用计数页回收
Python3.9+ stdlib. `python3 btree.py ops.json output.json`
模拟 B+ 树页管理: ops `[{"op":"put","k","v"},{"op":"snap","id"},{"op":"release","id"},{"op":"iter","snap_id","out":"key"}]`
- 页=4键容量; put 触发分裂/合并时旧页 COW 新版本; 页持有快照引用计数>0 不得回收
- iter 在 snap 上得到该快照一致键序(与当时put结果无关之后写入); release 后页批量回收
- 输出 `{"pages_live":n,"reclaimed":n,"iters":{sid:[keys]},"errors":[],"stats":{"allocs":n,"frees":n,"peak":n}}`
缺陷基线: 无快照,页立即回收,iter 读活页(受后续写影响).
