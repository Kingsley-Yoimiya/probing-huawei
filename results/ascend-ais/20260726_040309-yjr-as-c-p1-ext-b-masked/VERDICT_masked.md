# Verdict — 20260726_040309-yjr-as-c-p1-ext-b-masked (masked)

- recipes: `project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- dose: `masked` (D1 thr from recipes accept_min_ratio)

| case | C1/C0 | d_level | target | truth | notes |
|---|---:|---:|---|---|---|
| P1-EXT-B | 1.07 | D3 | rank_7 | rank_7 | d1_thr=1.05 (recipes:project/probing-huawei/scripts/fail-slow/dose_recipes.yaml: |

- 工具=`offline_training_metrics`（训练内 compute/wait/data）；Probing SQL = SQL_PENDING
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）
- CSV: `project/probing-huawei/results/ascend-ais/20260726_040309-yjr-as-c-p1-ext-b-masked/scoring_table_masked.csv`
