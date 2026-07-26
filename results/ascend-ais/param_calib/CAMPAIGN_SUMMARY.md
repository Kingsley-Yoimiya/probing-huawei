# Param-Calib 战役收官

> 关闭：2026-07-27 06:54+08 · status=**CLOSED**（主队列批次1–4 齐）  
> 方案：`project/reading-paper/writing/probing-paper/PILLAR-C-PARAM-CALIBRATION-PLAN.md`  
> 队列 / 台账：`docs/fail-slow/PARAM_CALIB_QUEUE.md` · `docs/fail-slow/ledger.md` §4.1b

## 标定结论（入库）

| Exp | 参数结论 | 落点 |
|-----|----------|------|
| ①-A | θ*=loud **1.16** / quiet **1.12** / masked **1.04** | `1A_dose_threshold/` |
| ①-B | θ*=**1.2** · φ*=**0.4** | `1B_localize_threshold/` |
| ②-B | torch_trace 环默认 **10 MB** | `2B_ring_capacity/` |
| P-FIX | cpu.util 环 **8 MiB**；尖刺 top=0.618s | `_prep/pillar_c_gate/P_FIX.md`（本目录副本见下） |
| ②-A | W*=**100**（跨 case max） | `2A_trace_window/` |
| ③-A | rate*=**0.001** | `3A_upgrade_rate/` |
| ③-B | SET→够 TT ≤**12**（S1≈150） | `3B_upgrade_latency/` |
| 健康判据 | **LOCKED**（复用 ①-A/①-B） | `4_health_summary_criteria/` |
| ④-A | volume_ratio≈**0.0626**（~16×） | `4A_federated_denoise/` |
| ④-B | N≥**17**→Coordinator | `4B_fanout_latency/` |
| ②-C | `local_retain_trigger_then_aggregate` | `2C_local_vs_preagg/` |
| ③-C | `local_suspect_only` · ratio **0.0625** | `3C_local_vs_global_upgrade/` |

状态快照：`LOOP_LAST_PARAM_CALIB.md`（同目录）。

## 备份

| 层 | 路径 | 说明 |
|----|------|------|
| 本机瘦身 tar | `myportal/results/ascend-ais/_backup/param_calib-slim-*.tar.gz` | PARAM/DONE/CRITERIA/logs；不含 probing_data / `.pull.tar` |
| P-FIX 瘦身 | `myportal/results/ascend-ais/_backup/p_fix-003642-slim-*.tar.gz` | SUMMARY + inspect JSON |
| AFS 全量 | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/param_calib/` | 含 ②-A/③-A 大 dump（~26G 量级） |
| Git | 仅 PARAM/DONE/CRITERIA/md/json + 小日志；大 dump 见 `.gitignore` | |

## 身份与资源

- kube：`songyiyang.p`（只借访问）· 落盘：`yinjinrun.p-huawei`
- 算力：`grj-megatron-32card-0716` hold-exec（让路策略）
- 禁止：a3-megatron / 删 grj vcjob / 写宋盘·geruijun
