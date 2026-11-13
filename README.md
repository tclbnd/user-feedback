# 跨分区分录对账

Python 3.9+，标准库实现，全程整数运算、无浮点。

## 命令

- `python3 reconcile.py ROOT apply SOURCE.json [--crash-before-publish]`：摄入一个批次并重发布报表。
- `python3 reconcile.py ROOT inspect [--version N]`：查看最新报表或任一已发布历史版本。
- `python3 reconcile.py ROOT trace TXN`：把一个交易从总额追溯到每条原始分录及其有效/失效版本。
- `python3 verify.py ROOT [--version N]`：独立整数账本重算并核对发布报表（不 import reconcile.py）。
- `python3 acceptance_test.py`：端到端验收（46 项断言，含乱序、崩溃续跑、追溯）。

## 输入契约

每次 apply 接受一批 JSON entries，currency_scale 映射币种到最小单位小数位，已有状态中同币种精度不变，冲突直接报错（退出 2）。分录含 id、rev、txn、date（YYYY-MM-DD）、currency、account、side（debit 或 credit）、amount（无指数非负十进制字符串）、source、line。可选 void=true 或 reverses=被回冲分录 id。逐 id 保留最高 rev，重复输入幂等，同版本不同内容报错（退出 2）。金额无法准确转最小单位时报 BAD_AMOUNT（含币种未声明精度）。void 不参与余额但原始行保留可查。借方为正，贷方为负，所有计数均为最小单位整数。

## 账本规则

先对未撤销原始分录求有效账本，再按 (date, id) 稳定排序处理回冲。同一原分录可被分次回冲，每次按剩余额校验。回冲方向必须相反（side 不同），币种与账户一致，不得早于原分录，不得指向回冲分录或超过原分录剩余额。失败分录不进余额，exceptions 项为 `{id,code,source,line}`，code 为 BAD_AMOUNT、REVERSAL_TARGET、REVERSAL_MISMATCH 或 REVERSAL_EXCESS。缺目标时可在新分区到达后重算（全量重放，无顺序依赖）。按 txn 与币种分别净额，跨币种 txn 列入 cross_currency_transactions，不能用另一币种抵销。balance、daily、monthly、unbalanced 全部按最小单位整数计数，保留数值为 0 的已出现键。

迟到回冲记在回冲自身日期：只影响该交易及当日/当月汇总，原分录与历史版本报表不变。

## 发布与恢复

ROOT 布局：`store/`（currency_scale、entries、批次指纹，全部原子写）与 `reports/`（不可变的 vN.json、moves-vN.json、store-vN.json 时点快照，以及 latest.json 指针）。发布顺序为：先写不可变版本文件与时点快照，再更新可变 store，最后原子翻转 latest.json（提交点）。首次成功发布版本 1，批次按原始字节指纹去重，重复投递不增加版本。`--crash-before-publish` 在持久化新版本前退出 97，旧报表完整，重试可恢复。所有输出用 sort_keys + 固定缩进序列化，排序键固定，结果可复现。

## 报表字段

`version`、`balances=[{account,currency,minor}]`、`daily=[{date,account,currency,minor}]`、`monthly=[{month,account,currency,minor}]`、`transactions=[{txn,cross_currency,currencies:[{currency,minor,status}]}]`（status 为 balanced/unbalanced/no_effective_entry）、`unbalanced=[{txn,currency,minor}]` 只保留非零、`cross_currency_transactions`、`exceptions`、`lineage=[{txn,id,rev,source,line}]`（实际参与对账的来源行）、`corrections=[{id,kind,minor_before,minor_after,delta,txn,source,line}]`（相邻版本的逐行修订差分，kind 为 NEW/CHANGED/REMOVED）。排序分别按 (account,currency)、(date,account,currency)、(month,account,currency)、txn、(txn,currency)、(id,code)、(txn,id)。inspect --version N 可复查任一已发布版本；verify 可独立重算任一历史版本的时点快照。

## trace 输出

`trace TXN` 返回该交易每条原始分录（含被取代的旧 rev、void、坏金额、被拒回冲），标注 effective 与 reason（OK / VOIDED / BAD_AMOUNT / REVERSAL_*）、effective_minor、source 与 line，并按币种汇总 net_minor 与 balanced/unbalanced 状态，实现从总额到原始行号与有效版本的完整追溯。

