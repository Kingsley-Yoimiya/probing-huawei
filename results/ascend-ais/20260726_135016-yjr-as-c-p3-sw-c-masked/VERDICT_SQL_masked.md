# Verdict SQL — 20260726_135016-yjr-as-c-p3-sw-c-masked (masked)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P3-SW-C | 1.65 | **D3** | SQL_PENDING | d1_thr=1.05 (recipes:project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P3-SW-C.masked); D1: C1/C0_step_ms=1.65 (thr=1.05); D2: IoU= |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `project/probing-huawei/results/ascend-ais/20260726_135016-yjr-as-c-p3-sw-c-masked/scoring_table_SQL_masked.csv`
