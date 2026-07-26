# Acceptance (loud): P3-EXT-C

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `/tmp/yjr_fs_orch_20260726_122130/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 84.29 | 1.00 |
| C1_inject_none | 147.01 | 1.74 |
| C2_probing | 158.10 | 1.88 |

C1/C0 = 1.744
C2/C0 = 1.876
