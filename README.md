# 原生 PDF 续表还原

Python 3.9+，依赖 requirements.txt。执行 `python3 table_pdf.py demo.pdf output.json --batch-pages 2`。输入是有文本层的合成 PDF，不做 OCR。每页可有两块平行表格。表头为 KEY、DESCRIPTION、AMOUNT，按同一基线的坐标建立列位置；重复表头、标题及页脚跳过。源 PDF 文本流可乱序。读页顺序为页码，再左栏到右栏，每栏由上至下。页面旋转要按原内容坐标校正，字段来源报告未旋转页面的原 PDF 坐标。可用 pypdf 的 visitor 获取文本及变换。

同一行的字段按基线 y 分组，允许 y 小幅漂移。下一行无 KEY 且仅有 DESCRIPTION 时视为前行续行。新一栏或新页顶部的 CONT: 只能接到阅读顺序中上一条尚未闭合的描述，文本间用单空格拼接。有效 KEY 形如 R000，金额为整数或空值。若同列在同一行存在两个不同金额，或续行无有效归属，隔离为 rejected（reason=AMBIGUOUS_AMOUNT 或 ORPHAN_CONTINUATION），不得误挂到别的记录。

输出 {rows,rejected}。rows 项 {id,description,amount,source}，source 把 id、description、amount 映射到一或多个 {page,x,y} 字典，页从 1 开始，x/y 为各文本段基线起点（pt）。缺值 source.amount=[]。rejected 项至少含 reason 和 page。不同 --batch-pages 的结果一致。演示 PDF 包含跨页续行、换栏、乱序和旋转。
