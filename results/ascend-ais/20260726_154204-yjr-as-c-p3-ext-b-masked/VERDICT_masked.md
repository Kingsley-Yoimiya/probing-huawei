# Verdict — 20260726_154204-yjr-as-c-p3-ext-b-masked (masked)

- recipes: `/Users/yinjinrun/Codespace/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- dose: `masked` (D1 thr from recipes accept_min_ratio)

| case | C1/C0 | d_level | target | truth | notes |
|---|---:|---:|---|---|---|
| P3-EXT-B | 1.08 | D3 | rank_13 | rank_7 | d1_thr=1.05 (dose_default:masked); D1: C1/C0_step_ms=1.08 (thr=1.05); D2: IoU=1. |

- 工具=`offline_training_metrics`（训练内 compute/wait/data）；Probing SQL = SQL_PENDING
- Greyhound / XPUTimer = PENDING（见 ledger §3.2；未接入≠D0，也未定谳 ENV-BLOCKED）
- CSV: `/Users/yinjinrun/Codespace/probing-huawei/results/ascend-ais/20260726_154204-yjr-as-c-p3-ext-b-masked/scoring_table_masked.csv`
