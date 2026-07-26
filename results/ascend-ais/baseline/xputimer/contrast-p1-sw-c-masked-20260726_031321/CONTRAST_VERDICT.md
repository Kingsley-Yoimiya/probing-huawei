# XPUTimer contrast · P1-SW-C masked

- case_id: `P1-SW-C`
- case_ref: `20260726_025116-yjr-as-c-p1-sw-c-masked`
- dose: `masked` — INLINE 2c n=768 every=4 fallback_s=0.05 victim=7; window [100,300]; gold tip max≈2.61
- detect_mode: **cross_run_contrast** （自主=prom hang/slow flags；跨-run=C1/C0 中位比≥1.05，需外部健康基线 C0）
- metric: jsonl `dur_us` of `HcclAllReduce` (host-wall around Hccl*)

## A) XPUTimer 自主信号（.prom hang/slow flags；无需外部基线）

| arm | coll_events | hang_flags | slow_flags |
|-----|-----------:|-----------:|-----------:|
| C0  | 81401 | 0 | 0 |
| C1  | 81402 | 0 | 0 |

**autonomous_flag (C1 hang+slow>0) = False** （SLOW_REPORT_US=0 关、HANG_TIMEOUT_MS=60000；未开 oracle INJECT_STALL）

## B) cross-run 中位对照（需外部健康基线 C0，非自主）

| arm | n | median dur_us | ≥1.5×C0med（噪声诊断，非判据） |
|-----|--:|-------------:|------------------------------:|
| C0  | 70336 | 116.0 | 3405 |
| C1  | 70336 | 114.0 | 4744 |

**C1/C0 coll ratio = 0.983** → FAIL (thr 1.05)

> ⚠️ `≥1.5×C0med` 计数仅作噪声诊断：C0 健康线自身就有 3405 个，说明该线在集合通信 host-wall 上可能大面积误报，**不作判据**。

## C) dose_check（step_ms 窗内中位；非 XPUTimer 规则）

- window: [100, 300)
- C0 median step_ms: 76.120 (n=3200)
- C1 median step_ms: 76.538 (n=3200)
- C1/C0 step_ms = 1.005 → FAIL/NA (thr 1.05)


## C2) tip/max dose_check（victim local_rank；P1-SW-C 叙事）

- victim local_rank=7
- median C1/C0 step_ms = 1.008 （常盲）
- p99 C1/C0 = 2.839
- max C1/C0 = 2.462 （C1 max=974.6 @step 100；C0 max=395.9）
- tip gate → PASS (med≥1.3 OR p99≥1.5 OR max≥2.5)
- Probing gold tip max≈2.61；本对照 tip max_ratio=2.462
## Verdict

- **autonomous_detect**: NO (XPUTimer 自己的 hang/slow flags)
- **cross_run_contrast**: FAIL (C1/C0=0.983；需外部基线)
- **dose_check**: FAIL/NA (step_ms C1/C0=1.005)
- **detect_ok**: false (autonomous OR cross_run；dose_check 单独记)
- **detect_mode**: `cross_run_contrast`

Note: 如实记能力边界；无咬合也是 DONE。不改对手阈值、不覆盖 Probing 分。
