# Verdict — 20260726_065841-yjr-as-c-p3-ext-b-quiet (quiet)

- recipes: `/Users/yinjinrun/Codespace/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- dose: `quiet` (D1 thr from recipes accept_min_ratio)

| case | C1/C0 | d_level | target | truth | notes |
|---|---:|---:|---|---|---|
| P3-EXT-B | 1.71 | D3 | rank_8 | rank_7 | d1_thr=1.15 (recipes:/Users/yinjinrun/Codespace/probing-huawei/scripts/fail-slow |

- 工具=`offline_training_metrics`（训练内 compute/wait/data）；Probing SQL = SQL_PENDING
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）
- CSV: `/Users/yinjinrun/Codespace/probing-huawei/results/ascend-ais/20260726_065841-yjr-as-c-p3-ext-b-quiet/scoring_table_quiet.csv`
