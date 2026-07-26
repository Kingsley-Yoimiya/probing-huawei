# Verdict SQL — 20260726_044454-yjr-as-c-p1-sw-a-masked (masked)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P1-SW-A | 1.26 | **D3** | SQL_NO_EXT_EVIDENCE | d1_thr=1.05 (recipes:/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P1-SW-A.masked); D1: C1/ |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `results/ascend-ais/20260726_044454-yjr-as-c-p1-sw-a-masked/scoring_table_SQL_masked.csv`
