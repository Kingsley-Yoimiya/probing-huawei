# XPUTimer contrast · P3-EXT-C masked

- case_id: `P3-EXT-C`
- case_ref: `20260726_122130-yjr-as-c-p3-ext-c-masked`
- dose: `masked` — stress_vm vm_n=32,vm_bytes=4G victim=7; window [100,300]; gold≈1.744
- detect_mode: **cross_run_contrast** （自主=prom hang/slow flags；跨-run=C1/C0 中位比≥1.05，需外部健康基线 C0）
- metric: jsonl `dur_us` of `HcclAllReduce` (host-wall around Hccl*)

## A) XPUTimer 自主信号（.prom hang/slow flags；无需外部基线）

| arm | coll_events | hang_flags | slow_flags |
|-----|-----------:|-----------:|-----------:|
| C0  | 81401 | 0 | 0 |
| C1  | 81401 | 0 | 0 |

**autonomous_flag (C1 hang+slow>0) = False** （SLOW_REPORT_US=0 关、HANG_TIMEOUT_MS=60000；未开 oracle INJECT_STALL）

## B) cross-run 中位对照（需外部健康基线 C0，非自主）

| arm | n | median dur_us | ≥1.5×C0med（噪声诊断，非判据） |
|-----|--:|-------------:|------------------------------:|
| C0  | 70336 | 126.0 | 10283 |
| C1  | 70336 | 138.0 | 15056 |

**C1/C0 coll ratio = 1.095** → PASS (thr 1.05)

> ⚠️ `≥1.5×C0med` 计数仅作噪声诊断：C0 健康线自身就有 10283 个，说明该线在集合通信 host-wall 上可能大面积误报，**不作判据**。

## C) dose_check（step_ms 窗内中位；非 XPUTimer 规则）

- window: [100, 300)
- C0 median step_ms: 154.123 (n=3200)
- C1 median step_ms: 161.404 (n=3200)
- C1/C0 step_ms = 1.047 → FAIL/NA (thr 1.05)

## Verdict

- **autonomous_detect**: NO (XPUTimer 自己的 hang/slow flags)
- **cross_run_contrast**: PASS (C1/C0=1.095；需外部基线)
- **dose_check**: FAIL/NA (step_ms C1/C0=1.047)
- **detect_ok**: true (autonomous OR cross_run；dose_check 单独记)
- **detect_mode**: `cross_run_contrast`

Note: 如实记能力边界；无咬合也是 DONE。不改对手阈值、不覆盖 Probing 分。
