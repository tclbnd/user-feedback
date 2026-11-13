# 多版本离线表单采集

Python 3.9+。执行 `python3 generate.py`，再运行 `python3 forms.py ROOT collect jobs.json`。处理合成 HTML 快照，无网络。jobs 含 {id,snapshots}，相对路径按渲染时间先后排列。form 的 data-template 为 v1、v2 或 v3，data-ready="true" 才可提取。选择首个 ready 快照。未就绪进入 TIMEOUT；未知版本进入 UNKNOWN_TEMPLATE。区分表单视图和结果发布。字段来自 input 的 value，DOM ID 可作为来源；按有效的 aria-label 或 label for 关联标签。隐藏控件和 disabled 控件忽略。区分同标签但不同 fieldset。v1/v2/v3 中 Buyer 组的 Name 或 Full name 是 buyer，Contact 组的同名字段是 contact。Routing 组的 Route 或 Route code 是 route；仅在 form data-routing="on" 时必需，否则输出 null。模板和夹具可扩展，但不能按位置猜字段。一个必填字段有多个有效候选时进入 AMBIGUOUS_FIELD，缺失进入 MISSING_FIELD。

结果为 rows（按 id 的 {id,template,fields,sources}）、review（按 id 的 {id,reason,file}）；fields 只含 buyer、contact、route，sources 对应 DOM ID，无来源为 null。记录可增加版本、重试及定位信息。inspect 返回已完成的视图。相同任务和相同输入重复采集不增加重复数据。--crash during-extract 首个任务字段解析中途退出 97；after-save 首个任务完整持久化后但对调用方报告前退出 97。正常重跑补齐所有任务，公开结果不得有半条。
