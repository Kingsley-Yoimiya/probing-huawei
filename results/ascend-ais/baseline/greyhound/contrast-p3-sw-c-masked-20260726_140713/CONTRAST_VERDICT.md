# Greyhound Contrast · P3-SW-C masked

- run_id: `contrast-p3-sw-c-masked-20260726_140713`
- case_ref: `20260726_135016-yjr-as-c-p3-sw-c-masked`
- dose_label: `masked`；dose: sidecar 8c cpu_n=80 cpu_load=70 mb=1 leak_every=2.0 max_chunks=32; window [100,300]; gold≈1.649；窗对齐 Case [100,300]
- fairness: `collect_seq` 真实 per-rank 序列 + C0 假阳性对照
- detect_ok: **no**；detect_mode: **no_bite**（自主= coll 比≥1.05 或 Rbeast 变点[C1有/C0无]）
- oracle_trigger: **no**（未把注入窗写入判定）；注入窗 [100,300] 仅标注；step_ms 窗比=0.988（剂量核对，非 Greyhound 规则）
- preload: cyclecounter stub + libhcclprobe.so；Redis :16379；pod=`yysong-worker-2`

## A) autonomous · collect-min AllReduce host-wall

| arm | n | median dur_us |
|-----|--:|-------------:|
| C0  | 70336 | 127.1 |
| C1  | 70336 | 129.9 |

**C1/C0 coll ratio = 1.023** → FAIL (thr 1.05)

## B) autonomous · Greyhound Rbeast（真实 per-rank 序列；C0 假阳性对照）

> call_id 用真实 (op,count) 签名序列、call_time 用真实 t0（`collect_seq`），跑 Greyhound 自带 find_period+find_performance_drop。健康线 C0 同跑作对照。

| arm | rep_rank | n_calls | uniq_sig | acf_period | n_changepoints |
|-----|---------:|--------:|---------:|-----------:|---------------:|
| C0  | 600828 | 4401 | 10 | 8 | 0 |
| C1  | 605368 | 4401 | 10 | 8 | 0 |

- C1 changepoints: []
- C0 changepoints: []
- rbeast_hit (C1有/C0无): **False**
- error: C1=None C0=None

## C) dose check · step_ms in oracle window (not Greyhound rule)

| arm | window median step_ms |
|-----|----------------------:|
| C0  | 87.67 |
| C1  | 86.60 |
| C1/C0 | 0.988 → dose_WEAK |

## Verdict

- **autonomous_detect**: NO (coll_pass=False, rbeast_hit=False, rbeast_fp=False)
- **dose_reproduced**: NO/WEAK (step_ms)

Note: P3-SW-C 注入下 Greyhound 主路径是 CCL 时间戳+变点。若 coll/Rbeast 无咬合而 step_ms 有抬升，记能力边界，不焊 D4。
