# 本地分片对象落地

Python 3.9+。运行 `python3 make_demo.py` 后用 `python3 land.py ROOT land demo.json --workers 3`。inspect 返回当前已发布代次，inspect --generation NAME 可读取旧代次。generation 和对象 id 是安全单段路径。输入 manifest 包含 generation 及 objects。每个对象含 id、version、length、sha256（整对象）与 chunks。chunk 含 version、start、end（字节半开范围）、sha256（该范围）和 payload（Base64）。模拟短读或内容错误时，载荷可能不匹配长度或摘要。可选 retry（第二次尝试的 Base64），每个无效载荷至多重试一次。范围声明和版本不合法不可重试。片可乱序或重叠，重叠内容必须相等，未覆盖位置为缺片。冲突或缺片的对象隔离。

至少输出 generation、published、quarantine，按对象 id 排序。published 项含 id、length、sha256；ROOT/generation/id 保存已验证文件。quarantine 项含 id、reason。同一代次重复 land 保持相同结果，不能用不同 manifest 覆盖已发布代次。每个对象可记录范围、重试和隔离原因。始终先完成对象校验再原子发布代次指针，旧版本文件可读，未公开代次不能通过 inspect 读取。尚无已公开代次时返回 generation=null 及两个空列表。

持久化每个已验证范围。after-piece 在首个分片落盘后、范围登记之前退出 97。after-range 在首个范围登记完成后退出 97。before-pointer 在本代数据准备好、公开指针写入之前退出 97。带同一 manifest 重启可校验并复用可确认的片，统计 fetched_ranges（本轮获取的有效范围数）与 reused_ranges（本轮复用的范围数）。暂存清理不得破坏已发布代次。--workers 控制本地并发量。
