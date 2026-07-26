# Greyhound Contrast · P1-SW-C quiet

- run_id: `contrast-p1-sw-c-quiet-20260726_022611`
- case_ref: `20260726_021606-yjr-as-c-p1-sw-c-quiet`
- dose_label: `quiet`；dose: INLINE 2c n=768 every=4 fallback_s=0.1 victim=7; window [100,300]; gold tip max≈2.61；窗对齐 Case [100,300]
- fairness: `collect_seq` 真实 per-rank 序列 + C0 假阳性对照
- detect_ok: **no**；detect_mode: **no_bite**（自主= coll 比≥1.15 或 Rbeast 变点[C1有/C0无]）
- oracle_trigger: **no**（未把注入窗写入判定）；注入窗 [100,300] 仅标注；step_ms 窗比=1.006（剂量核对，非 Greyhound 规则）
- preload: cyclecounter stub + libhcclprobe.so；Redis :16379；pod=`yysong-worker-2`

## A) autonomous · collect-min AllReduce host-wall

| arm | n | median dur_us |
|-----|--:|-------------:|
| C0  | 70336 | 108.0 |
| C1  | 70336 | 111.1 |

**C1/C0 coll ratio = 1.029** → FAIL (thr 1.15)

## B) autonomous · Greyhound Rbeast（真实 per-rank 序列；C0 假阳性对照）

> call_id 用真实 (op,count) 签名序列、call_time 用真实 t0（`collect_seq`），跑 Greyhound 自带 find_period+find_performance_drop。健康线 C0 同跑作对照。

| arm | rep_rank | n_calls | uniq_sig | acf_period | n_changepoints |
|-----|---------:|--------:|---------:|-----------:|---------------:|
| C0  | 329517 | 4401 | 10 | 8 | 0 |
| C1  | 334015 | 4401 | 10 | 8 | 0 |

- C1 changepoints: []
- C0 changepoints: []
- rbeast_hit (C1有/C0无): **False**
- error: C1=None C0=None

## C) dose check · step_ms in oracle window (not Greyhound rule)

| arm | window median step_ms |
|-----|----------------------:|
| C0  | 76.11 |
| C1  | 76.58 |
| C1/C0 | 1.006 → dose_WEAK |


## C2) tip/max dose_check（victim local_rank；P1-SW-C 叙事）

- victim local_rank=7
- median C1/C0 step_ms = 1.005 （常盲）
- p99 C1/C0 = 0.743
- max C1/C0 = 2.727 （C1 max=1319.7 @step 100；C0 max=484.0）
- tip gate → PASS (med≥1.15 OR p99≥1.5 OR max≥2.5)
- Probing gold tip max≈2.61；本对照 tip max_ratio=2.727
## Verdict

- **autonomous_detect**: NO (coll_pass=False, rbeast_hit=False, rbeast_fp=False)
- **dose_reproduced**: YES (tip/max；median 盲)

Note: P1-SW-C 注入下 Greyhound 主路径是 CCL 时间戳+变点。若 coll/Rbeast 无咬合而 step_ms 有抬升，记能力边界，不焊 D4。
