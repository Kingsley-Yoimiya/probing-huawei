# SUMMARY · P3-EXT-C stress_vm Quiet · SCORED D3

| 项 | 值 |
|----|-----|
| 状态 | **SCORED** |
| run_id | `20260726_102936-yjr-as-c-p3-ext-c-quiet` |
| pod | `grj-megatron-32card-0716-master-0`（hold-exec / 跳板 nohup） |
| world | 16（1×16） |
| dose | quiet `stress-ng --vm 32 --vm-bytes 4G --vm-keep --page-in` |
| inject | `stress_vm` host_bound |
| C0 / C1 / C2 med step_ms | 83.21 / 158.58 / 90.61 |
| C1/C0 | **1.906**（thr 1.15）→ Quiet **PASS** |
| **最高 D** | **D3**（offline；DUMP=0 → SQL_PENDING，不升 D4） |
| jsonl | 48 |
| Loud 金标 | `20260725_021906` 96×6G C1/C0=1.59 D3（冻结方案只复测） |
| pilot | `20260726_095909` C1/C0=1.722 CALIBRATED |
| stubs | `100606` / `101737` C0_noise ineffective 保留 |

## 三问

1. **边界**：manifest（16 卡、host_bound、stress_vm、victim_lr=7、C0–C2、dose=quiet）
2. **跑通**：jsonl 48 + stress_vm dispatch + ACCEPT PASS
3. **检出**：如实 **D3**（未回调 SQL/阈值；GH/XPU quiet 已入队 PENDING）
