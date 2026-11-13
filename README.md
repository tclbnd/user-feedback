# 帧精确智能拼接

Python 3.9+，依赖 FFmpeg（PATH 或 ZQ_FFMPEG）。执行 `python3 cut.py demo.json output.mp4 report.json`。本地 sources 已生成，make_demo.py 可重建。EDL 含 sources={名字:相对 MP4} 与 clips=[{source,start_ms,end_ms}]。开始包含，结束排除，单位是以源第一显示帧为 0 的整毫秒。成片片段连续，某一源帧的成片 PTS 为前片累计时长+源 PTS−片段 start_ms。允许首个选中帧在片段起点后。保留 VFR，无重复补帧。支持 H.264 B 帧和部分开放 GOP。所有素材 64×48，画面最上方 6 行的前 36 像素以 12 个三像素宽亮度块表示整数帧编号，最低位在左。

完全处在片段内且可独立解码、编码参数相容的闭合 GOP 保留原视频包。切口和不相容依赖重编码。输出单调 DTS，按精确时间基重排 PTS。音频 AAC 源均 48 kHz mono。按源可听采样裁剪，丢弃编码 priming 和 padding，接在成片片段起点。必要时可重编码音频，完整解码后可听样本个数和脉冲相位应与编辑表一致。报告 frames 为成片显示顺序 [{source,frame_id,source_pts_ms,output_pts_ms,copied}]，audio 每片记录 {source,start_sample,end_sample,output_start_sample}，均为去除编码填充后的坐标。要求输出真实可播放 MP4。可增加重新编码范围及原因。
