# Verdict SQL — 20260726_025116-yjr-as-c-p1-sw-c-masked (masked)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P1-SW-C | 2.61 | **D3** | SQL_NO_EXT_EVIDENCE | d1_thr=1.05 (recipes:/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P1-SW-C.masked); D1: tip |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `project/probing-huawei/results/ascend-ais/20260726_025116-yjr-as-c-p1-sw-c-masked/scoring_table_SQL_masked.csv`
