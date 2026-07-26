# Verdict SQL — 20260726_065841-yjr-as-c-p3-ext-b-quiet (quiet)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P3-EXT-B | 1.71 | **D3** | SQL_PENDING | d1_thr=1.15 (recipes:/Users/yinjinrun/Codespace/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P3-EXT-B.quiet); D1: C1/C0_step_ms=1.71 ( |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `/Users/yinjinrun/Codespace/probing-huawei/results/ascend-ais/20260726_065841-yjr-as-c-p3-ext-b-quiet/scoring_table_SQL_quiet.csv`
