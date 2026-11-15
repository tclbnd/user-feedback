# 延迟不透明+前向透明混合管线
`python3 pipe.py req.json output.json`
req.json: `{"opaque":[{z,albedo}],"trans":[{z,alpha,tint}],"lights":[{dir,color}],"orders":3}`
- G-Buffer 只装不透明; 透明读其深度做遮挡; 两通道同一光照模型
- 透明排序无关混合(premult/over,按深度排序); 输出 `{"color":[r,g,b,a],"per_light":[[v]],"occ":n,"digest"}`
缺陷基线: 透明写G-Buffer(错遮挡)+提交顺序混合.
