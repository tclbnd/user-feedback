# 结构相似度参考实现的窗函数与下采样约定

`python3 ssim.py input.json output.json`

input.json: `{"ref":[[h x w 0-255]],"dist":[[..]],"window":"gauss|uniform","scales":1|3,"block":1}`

模型约定：
- SSIM 常数 K1=0.01,K2=0.03,L=255 → C1=6.5025,C2=58.5225,C3=C2/2
- gauss 模式:11 点 σ=1.5 高斯加权逐像素滑动窗；uniform 模式:8x8 均匀窗步进1
- scales=3 时按 2 倍逐级下采样(均值池化,边界丢弃奇余)，逐级算 cs 图，末级算 ssim
- 输出 `{"ssim":float,"map":[[末级图]],"per_scale":[每级均值],"digest":"sha1"}`；block 仅内部并行划分不改结果

缺陷基线：固定均匀窗步进4、常数错用 1、无多尺度。
