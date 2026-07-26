# XPUTimer contrast · P3-SW-B quiet

- case_id: `P3-SW-B`
- case_ref: `20260725_232814-yjr-as-c-p3-sw-b-quiet`
- dose: `quiet` — INLINE 8b mb=8 stall_s=0.1 victim=7; window [100,300]; gold≈2.101
- detect_mode: **cross_run_contrast** （自主=prom hang/slow flags；跨-run=C1/C0 中位比≥1.15，需外部健康基线 C0）
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
| C0  | 70336 | 126.0 | 10382 |
| C1  | 70336 | 128.0 | 11361 |

**C1/C0 coll ratio = 1.016** → FAIL (thr 1.15)

> ⚠️ `≥1.5×C0med` 计数仅作噪声诊断：C0 健康线自身就有 10382 个，说明该线在集合通信 host-wall 上可能大面积误报，**不作判据**。

## C) dose_check（step_ms 窗内中位；非 XPUTimer 规则）

- window: [100, 300)
- C0 median step_ms: 157.564 (n=3200)
- C1 median step_ms: 177.291 (n=3200)
- C1/C0 step_ms = 1.125 → FAIL/NA (thr 1.15)

## Verdict

- **autonomous_detect**: NO (XPUTimer 自己的 hang/slow flags)
- **cross_run_contrast**: FAIL (C1/C0=1.016；需外部基线)
- **dose_check**: FAIL/NA (step_ms C1/C0=1.125)
- **detect_ok**: false (autonomous OR cross_run；dose_check 单独记)
- **detect_mode**: `cross_run_contrast`

Note: 如实记能力边界；无咬合也是 DONE。不改对手阈值、不覆盖 Probing 分。
