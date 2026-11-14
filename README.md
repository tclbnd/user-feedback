# B+ 树快照引用计数页回收

页式 MVCC B+ 树: 引用计数页回收 + COW 分裂/合并 + 快照固定迭代。Python 3.9+ 标准库。

## 文件

- `btree.py` —— 核心实现(`PageStore` 页分配器 + `MVCCBTree` 多版本 B+ 树)与 CLI。
- `stress.py` —— 多线程并发读写压测(多写者 COW + 多读者快照迭代), 输出统计与判定。
- `acceptance.py` —— 逐条对照两条验收标准的证据脚本。
- `test_poison.py` —— 毒化检疫对抗测试(悬挂访问/double-free/pid 不复用/延迟回收)。
- `asan_harness.cpp` —— 等价生命周期模型的 C++ ASAN/UBSAN/LSAN harness。
- `ops.json` / `output.json` —— CLI 示例输入与输出。

## 核心机制

1. **页引用计数**: 每页有全局唯一、永不复用的 pid 与 `refcnt`。树侧不变量为
   “从当前根可达的页恰持 1 个树引用”; 每个快照对其钉住的页各持 1 个引用。
2. **COW 写入**: put/delete 沿根→叶复制受影响路径, 旧版本页按引用计数决定生死——
   无快照钉住时立即回收, 有快照时 `refcnt` 不归零, 延迟到快照 `release` 批量回收。
   提交采用“可达性增量 + 每事务分配集(stash)”: 新进入活树的新页 ref, 脱离活树的
   旧页 unref, COW 共享页计数不动; 构造期产生的未挂树垃圾页由 live 集合守卫回收。
3. **毒化检疫**: 回收即从活页表删除并把 pid 移入 quarantine, 之后任何 `access/unref`
   都被守卫捕获, 累计 `uaf_violations/double_free/refcount_violations` 并抛错。
4. **快照与迭代**: 快照对当时可达页集合逐个 ref 钉住; 迭代器从快照根做中序树下降,
   只访问钉住的页, 与后续写入完全隔离, 输出严格有序键序。删除路径在每次提交后
   按子树实际最小键重算内页分隔键, 保证 B+ 树“分隔键 == 右孩子最小键”不变量。

## CLI

`python3 btree.py ops.json output.json`

ops: `put` / `del` / `snap` / `release` / `iter`(iter 必须指定存在的 `snap_id`,
不再回退活页)。输出含 `pages_live/reclaimed/iters/errors/batch_reclaims/stats`,
stats 中 `uaf_violations/double_free/refcount_violations` 健康时恒为 0,
全部快照释放且树销毁后 `frees == allocs`(回收率 100%)。

## 运行测试

```
python3 test_poison.py     # 毒化/引用计数对抗测试
python3 acceptance.py      # 两条验收标准证据(并发 + 双快照一致 + 100% 回收)
python3 stress.py --seconds 4 --writers 6 --readers 4
```

C++ 内存安全 harness(需 clang, 直接调用工具链二进制以绕过 xcrun 缓存):

```
CLANG=/Library/Developer/CommandLineTools/usr/bin/clang++
$CLANG -std=c++17 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
       asan_harness.cpp -o asan_harness && ./asan_harness
$CLANG -std=c++17 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
       -DASAN_MULTITHREAD asan_harness.cpp -o asan_harness_mt && ./asan_harness_mt
```

期望两个 harness 均退出码 0 且 ASAN/UBSAN/LSAN 无报告(`uaf=0 refcnt_bad=0`)。

## 设计要点(相对缺陷基线)

旧基线无真实页、无引用计数, release 无条件释放、迭代器回退活页。本实现:
页容量 4 键; 叶/内页 COW 分裂, 删除走借键/合并并刷新分隔键; 快照持有页不可回收;
迭代器固定快照版本; 释放后批量回收, 全程毒化守卫保证零悬挂访问。

