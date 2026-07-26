# ④-A DONE · `4A_federated_denoise`

> 原 BLOCKED 已解除：SUMMARY/DETAIL 两阶段 harness 已接线并离线跑出正式 PARAM。

## 解锁项对照

| 项 | 状态 |
|---|---|
| 健康摘要判据 LOCKED | ✅ |
| Phase-1 全 rank SUMMARY | ✅ `4a_federated_denoise.py` |
| 协调 dose+①-B → suspects | ✅ |
| Phase-2 仅 suspects DETAIL | ✅ |
| 对照臂朴素全 rank DETAIL | ✅ |
| volume_ratio + localize_culprit_ms | ✅ 见 PARAM.json |

## 关键数字

- **volume_ratio** (fed/naive) = **0.062580**（≈16.0×）
- **localize_culprit_ms** = **2.8428**（离线 harness 中位）
- mode = `offline_harness`；harness = `scripts/fail-slow/param_calib/4a_federated_denoise.py`

## 未做（有意）

- 未开 ④-B（基数×FanoutScope 网络延迟）
- 未上卡 live federation（离线已能量比+定位 CPU 墙钟；网络 RTT 属 ④-B）
