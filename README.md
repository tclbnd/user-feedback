# 文本更正入湖与确定性重放
用法 `python3 ot_sim.py input.json output.json`。输入含 `doc`（原始 Unicode 字符串）、`batches`（一组更正数组）、`orders`（若干数组，每个是所有批次下标的一次排列）。
插入事件为 `{"id":"ins1","source":2,"seq":0,"op":"ins","pos":3,"text":"新"}`，删除为 `{"id":"del1","source":1,"seq":9,"op":"del","pos":3,"len":2}`。所有坐标都是原始字符串的 Unicode 字符下标，插入位置可等于字符数，删除区间为半开且不得越界。插入按 (pos, -source, seq, id) 排序，插入文本先于该坐标的原字符，即使原字符被删也保留插入文本。每个原字符最多被删一次。
同一 id 的重复事件必须字段一致，有冲突时整次退出 2。输出 `{"final":[按 orders 顺序的重放全文],"converged":布尔,"dedupe":{"unique":整数,"duplicates":整数},"audit":[...]}`。
audit 按 id 排序，一条唯一更正对应一个 `{"id":字符串,"op":字符串,"copies":整数,"outcome":"applied" 或 "merged"}`。插入均为 applied。按 (pos, -source, seq, id) 处理删除，首次覆盖任一原字符为 applied，否则为 merged。改变批边界不应影响这些输出字段。
现有代码按到达顺序直接对已经编辑过的字符串操作，缺原坐标转换、去重和冲突处理。
