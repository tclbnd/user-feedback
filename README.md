# 音频波形概览索引

Python 3.9+。原始音频为无头、交错的 signed 16-bit little-endian PCM。命令为 `python3 overview.py ROOT build SOURCE --channels N`、`python3 overview.py ROOT edit EDL.json`、`python3 overview.py ROOT query ranges.json`。build 校验帧对齐、保存源标识并构建多层 min/max/平方和/计数；首个版本为 1，初始 EDL 是整段源。复用已建索引，索引损坏时可重新 build 从源构建，源不匹配时 query 拒绝返回旧索引结果。可在 query 检查文件标识及修改时间，必要时重新核验摘要。所有采样坐标单位为帧，N 为 1–8。

EDL 为 {"segments":[{"start":0,"end":42},{"silence":17},...]}。源半开区间可重复或换序。零长段允许。edit 用新列表替换 EDL，版本递增，可选 --crash-before-publish 在新版本持久化公开前退出 97。旧版可用 query --version N 读取。edit 复用原始音频索引，不能重扫全源，允许只计算派生段边界。记录 edit 和 build 的重建计数。

ranges.json 为 {"ranges":[{"id":...,"start":...,"end":...}]}。坐标在当前 EDL 时间轴，start 和 end 可相等。返回 version、ranges，每项包含 id 和 channels，channels 按原声道顺序给出 min、max、rms。空区间三者为零。RMS 等于平方和除以帧数后开根。query 另外报告实际读取的 raw_bytes 和 index_pages（按所触及的 4096 字节索引页数）。不准用伪造指标或全文件读后只报告有效部分。给定等宽 64 个概览分段，音频长度扩大十倍后已建索引查询不得按源文件大小线性增长。
