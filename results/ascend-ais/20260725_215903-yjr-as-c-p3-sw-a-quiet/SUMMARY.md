# SUMMARY · P3-SW-A inline `8a` GC/stall Quiet · SCORED D4

| 项 | 值 |
|----|-----|
| 状态 | **PROBING_SCORED** |
| run_id | `20260725_215903-yjr-as-c-p3-sw-a-quiet` |
| pod | `grj-megatron-32card-0716-master-0`（hold-exec；未占 grj-w0） |
| world | 16（1×16） |
| dose | `INLINE_GC_EVERY=1, INLINE_GC_STALL_S=0.1`（quiet calibrated） |
| inject | `INLINE_INJECT=8a` victim_local=7；host_bound |
| C0 / C1 / C2 med step_ms | 155.64 / 303.33 / 371.24 |
| C1/C0 | **1.949**（thr 1.15）→ Quiet **PASS** |
| **最高 D** | **D4**（SQL：`cpu.utilization_rss` p3sw_rss_window；offline D3 定位 rank_7） |
| SQL dump | DUMP_OK（cpu util/tasks + p3sw_rss）；缺 process.* / torch_trace |
| 备注 | 终拉 tar 曾截断，已 gzip 补拉 C2；未开第二路；未回调 SQL/阈值 |

## 三问

1. **边界**：manifest（16 卡、host_bound、8a stall=0.1 every=1、victim_lr=7、C0–C2、dose=quiet）
2. **跑通**：jsonl 48 + INLINE 8a 激活 + ACCEPT_QUIET PASS
3. **检出**：如实 **D4**（窗 IoU=1；victim=7；RSS SQL PASS_D4）；Loud 冻结方案弱档复测
