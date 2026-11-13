# 跨分区分录对账

Python 3.9+。用 `python3 reconcile.py ROOT apply demo.json` 和 `python3 reconcile.py ROOT inspect`。每次 apply 接受一批 JSON entries，currency_scale 映射币种到最小单位小数位，已有状态中同币种精度不变。分录含 id、rev、txn、date（YYYY-MM-DD）、currency、account、side（debit 或 credit）、amount（无指数非负十进制字符串）、source、line。可选 void=true 或 reverses=被回冲分录 id。逐 id 保留最高 rev，重复输入幂等，同版本不同内容报错。金额无法准确转最小单位时报 BAD_AMOUNT。void 不参与余额。借方为正，贷方为负。

先对未撤销原始分录求有效账本，再按 date、id 排序处理回冲。同一原分录可被分次回冲。回冲方向必须相反，币种与账户一致，不得早于原分录，不得指向回冲或超过原分录剩余额。失败分录不进余额，exceptions 项为 {id,code,source,line}，code 为 BAD_AMOUNT、REVERSAL_TARGET、REVERSAL_MISMATCH 或 REVERSAL_EXCESS。缺目标时可在新分区到达后重算。按 txn 与币种分别净额，对跨币种 txn 附 cross_currency_transactions，不能用另一币种抵销。balance 或 unbalanced 中按最小单位整数计数。保留数值为 0 的已出现键。

结果为 version、balances、monthly、unbalanced、cross_currency_transactions、exceptions、lineage。balances=[{account,currency,minor}]，monthly 在余额字段外加 month（YYYY-MM）；unbalanced=[{txn,currency,minor}] 只保留非零；lineage=[{txn,id,rev,source,line}] 为实际参与对账的来源行。排序分别按 (account,currency)、(month,account,currency)、(txn,currency)、txn、id、(txn,id)。首次成功发布版本 1，批次重复不增加版本。inspect --version N 可复查已发布版本。--crash-before-publish 在持久化新版本前退出 97，旧报表完整，重试可恢复。结果可额外含按交易的修订差分。
