# 复合键外存连接

Python 3.9+。命令 `python3 join.py left.csv right.csv result --type left --memory-mib 64`。CSV 是 UTF-8 标准带引号格式。输入表头固定 key_a,key_b,value，键中的空字符串视为 SQL NULL，其他值逐字匹配。两侧均可重复行。支持 inner 和 left，左外连接的无匹配行 right_value 为空。输出 rows.csv 的表头为 key_a,key_b,left_value,right_value，验收按多重集比对，输出顺序由实现说明。只发布包含 rows.csv 和 _SUCCESS 的完整目录。结果目录已存在时拒绝覆盖。_SUCCESS 和标准输出是 JSON，至少包含 rows、spill_bytes、peak_rss_bytes。峰值为当前进程 RSS（字节），包含解释器；memory-mib 为工作预算。应限制暂存文件句柄数，并明确热点路径如何防止多对多结果在内存累积。

`--crash-before-publish` 在暂存结果写完、发布最终目录前无清理退出 97。目标目录此时不存在。无故障重跑可清理属于目标的上次暂存并安全重新发布，其他目录不得修改。任意 I/O 异常不得发布不完整结果。允许 SQLite 作为外存中间索引或实现显式分区与块连接；不要把结果全读回内存。
