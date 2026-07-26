# Verdict SQL — 20260725_215903-yjr-as-c-p3-sw-a-quiet (quiet)

| case | C1/C0 | d_level | SQL | notes |
|---|---:|---|---|---|
| P3-SW-A | 1.95 | **D4** | PASS_D4 | d1_thr=1.15 (recipes:/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P3-SW-A.quiet); D1: C1/C |

- 主证据：C2 `probing/query_manifest.json`；训练 jsonl 仅离线验证到 D3。
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）。
- CSV: `project/probing-huawei/results/ascend-ais/20260725_215903-yjr-as-c-p3-sw-a-quiet/scoring_table_SQL_quiet.csv`
