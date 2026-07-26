# Greyhound Contrast · P3-EXT-B quiet

- run_id: `contrast-p3-ext-b-quiet-20260726_071352`
- case_ref: `20260726_065841-yjr-as-c-p3-ext-b-quiet`
- dose_label: `quiet`；dose: stress_io fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256; engine=stress-ng; window [100,300]; gold≈1.709；窗对齐 Case [100,300]
- fairness: `collect_seq` 真实 per-rank 序列 + C0 假阳性对照
- detect_ok: **no**；detect_mode: **no_bite**（自主= coll 比≥1.15 或 Rbeast 变点[C1有/C0无]）
- oracle_trigger: **no**（未把注入窗写入判定）；注入窗 [100,300] 仅标注；step_ms 窗比=0.882（剂量核对，非 Greyhound 规则）
- preload: cyclecounter stub + libhcclprobe.so；Redis :16379；pod=`yysong-worker-2`

## A) autonomous · collect-min AllReduce host-wall

| arm | n | median dur_us |
|-----|--:|-------------:|
| C0  | 70336 | 123.0 |
| C1  | 70336 | 129.9 |

**C1/C0 coll ratio = 1.056** → FAIL (thr 1.15)

## B) autonomous · Greyhound Rbeast（真实 per-rank 序列；C0 假阳性对照）

> call_id 用真实 (op,count) 签名序列、call_time 用真实 t0（`collect_seq`），跑 Greyhound 自带 find_period+find_performance_drop。健康线 C0 同跑作对照。

| arm | rep_rank | n_calls | uniq_sig | acf_period | n_changepoints |
|-----|---------:|--------:|---------:|-----------:|---------------:|
| C0  | 625556 | 4401 | 10 | 8 | 0 |
| C1  | 629854 | 4401 | 10 | 8 | 0 |

- C1 changepoints: []
- C0 changepoints: []
- rbeast_hit (C1有/C0无): **False**
- error: C1=None C0=None

## C) dose check · step_ms in oracle window (not Greyhound rule)

| arm | window median step_ms |
|-----|----------------------:|
| C0  | 164.82 |
| C1  | 145.33 |
| C1/C0 | 0.882 → dose_WEAK |

## Verdict

- **autonomous_detect**: NO (coll_pass=False, rbeast_hit=False, rbeast_fp=False)
- **dose_reproduced**: NO/WEAK (step_ms)

Note: io_fallback=stress-ng hdd8+iomix4; no fio on w2
