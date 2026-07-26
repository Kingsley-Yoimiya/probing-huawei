# P3-SW-A masked formal · `20260725_224156`

- pod: `grj-megatron-32card-0716-master-0`（未占 grj-w0）
- dose: `inline_gc_every=1,inline_gc_stall_s=0.05`
- configs: C0+C1+C2；jsonl=48
- C0/C1/C2 med step_ms: 147.98 / 261.63 / 252.65
- **C1/C0 = 1.768** PASS（masked thr 1.05）
- offline: D3（victim rank_7；IoU=1）
- **最高 D = D4**（SQL `cpu.utilization_rss` PASS_D4）
- 未回调 SQL/阈值；hold 内建 ACCEPT_LOUD 误标 dose=loud，已以 `--dose masked` 重验
