# Verdict — 20260726_075912-yjr-as-c-p3-ext-a-quiet (quiet)

- recipes: `project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- dose: `quiet` (D1 thr from recipes accept_min_ratio)

| case | C1/C0 | d_level | target | truth | notes |
|---|---:|---:|---|---|---|
| P3-EXT-A | 1.26 | D3 | rank_2 | rank_7 | d1_thr=1.15 (recipes:project/probing-huawei/scripts/fail-slow/dose_recipes.yaml: |

- 工具=`offline_training_metrics`（训练内 compute/wait/data）；Probing SQL = SQL_PENDING
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）
- CSV: `project/probing-huawei/results/ascend-ais/20260726_075912-yjr-as-c-p3-ext-a-quiet/scoring_table_quiet.csv`
