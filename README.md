# 解析线段覆盖抗锯齿
`python3 aa.py req.json output.json`
req.json: `{"segs":[{x0,y0,x1,y1,w}],"size":64,"tile":32}`
- 逐像素解析覆盖=像素矩形与线段胶囊相交面积; 水平/垂直/退化段都要处理
- 共享端点不重复计覆盖(覆盖≤1); 输出 `{"cov":[[v]],"energy":v,"digest"}`
缺陷基线: 中心点采样(亚像素宽线段消失).
