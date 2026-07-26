# SUMMARY · P3-EXT-C stress_vm Masked · SCORED D3

| 项 | 值 |
|----|-----|
| 状态 | **SCORED** |
| run_id | `20260726_122130-yjr-as-c-p3-ext-c-masked` |
| pod | `grj-megatron-32card-0716-master-0`（hold-exec / 跳板 nohup） |
| world | 16（1×16） |
| dose | masked `stress-ng --vm 32 --vm-bytes 4G --vm-keep --page-in`（=quiet lean） |
| inject | `stress_vm` host_bound |
| C0 / C1 / C2 med step_ms | 84.29 / 147.01 / 158.10 |
| C1/C0 | **1.744**（thr 1.05）→ Masked **PASS** |
| **最高 D** | **D3**（offline；DUMP=0 → SQL_PENDING，不升 D4） |
| jsonl | 48 |
| Loud 金标 | `20260725_021906` 96×6G C1/C0=1.59 D3 |
| pilot | `20260726_110355` C1/C0=1.085 CALIBRATED |
| stubs | `104125`/`104844` C0_noise；`105713` 24×4G=1.014；formals C0_noise；`115744` C2_crash |

## 三问

1. **边界**：manifest（16 卡、host_bound、stress_vm、victim_lr=7、C0–C2、dose=masked）
2. **跑通**：jsonl 48 + stress_vm dispatch + ACCEPT PASS
3. **检出**：如实 **D3**（未回调 SQL/阈值；GH/XPU masked 已入队 PENDING）
