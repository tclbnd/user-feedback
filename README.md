# 陀螺仪稳像姿态解算
`python3 stab.py req.json output.json`
req.json: `{"gyro":[{t,wx,wy,wz}@1kHz],"frames":[t,...],"truth":[deg]真值,"drop":[t1,t2]丢包区间}`
- 帧 t 对齐最近 gyro 采样; 角速度积分→姿态角; 高通(τ=2s)分离运镜/抖动
- 丢包线性插值; 输出 `{"poses":[deg@帧],"residual_rms":v,"crop_ratio":v,"comp":[[3x3]@帧]}`
缺陷基线: 逐帧角速度当姿态(无积分无平滑).
