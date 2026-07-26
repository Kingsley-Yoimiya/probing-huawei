# Verdict SQL — 20260726_154204-yjr-as-c-p3-ext-b-masked (masked)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P3-EXT-B | 1.08 | **D3** | SQL_PENDING | d1_thr=1.05 (dose_default:masked); D1: C1/C0_step_ms=1.08 (thr=1.05); D2: IoU=1.00 det=[100,300] gt=[100,300] onset=109; D3_signal=max_data_ |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `/Users/yinjinrun/Codespace/probing-huawei/results/ascend-ais/20260726_154204-yjr-as-c-p3-ext-b-masked/scoring_table_SQL_masked.csv`
