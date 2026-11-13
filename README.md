# 多时区业务日

Python 3.9+，使用 `python3 bizday.py state.json apply demo.json` 和 `python3 bizday.py state.json inspect`。按可重现的离线规则计算，每批输入含 tz_version、regions、events。regions 将地区映射到 tz_version 下的 {initial_offset_min, transitions}。transition 由 at_utc（ISO UTC Z）及 offset_min 组成，已按 UTC 升序。每个偏移区间左闭右开，初始偏移在首转折前有效。用这些固定偏移重建本地时间映射；无需宿主 tzdb。events 含 id、rev、region、local（YYYY-MM-DDTHH:MM:SS）、amount（两位精度十进制字符串）、可选 fold、offset_min、void。按 id 取最高 rev。批次可换序重复，旧修订不覆盖。相同修订不同内容应拒绝。

给定本地时间求所有相容 UTC 候选，按 UTC 升序，遇两候选必须给 fold=0 或 1，漏填或非法拒绝 AMBIGUOUS_LOCAL。无候选拒绝 NONEXISTENT_LOCAL。显式 offset_min 必须等于选中偏移，错则 OFFSET_MISMATCH。业务日为当地 00:00 到次日 00:00，UTC 边界由同一地区偏移表求解，本题日期边界均唯一。迟到或撤回更新它原来的地区与业务日期。金额精确到分。

返回 version、days、events、rejected。days 项为 {region,date,count,minor,utc_start,utc_end,seconds}，按地区与日期升序。events 是当前有效事实的 {id,rev,utc,region,date,minor} 按 id 排序。rejected 项为 {id,rev,code}，按 id 排序，void 跳过。每次有新事实或新 tz_version 时提交新版本，重复不变，inspect --version N 读取旧版本。更换规则时，除有规则变化的地区或本批事实有变更的地区外，无需重算其他地区。可增加 affected_regions 和对账差异。
