# Acceptance (masked): P3-EXT-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `masked`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `/tmp/yjr_fs_orch_20260726_154204/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 100.44 | 1.00 |
| C1_inject_none | 108.26 | 1.08 |
| C2_probing | 107.27 | 1.07 |

C1/C0 = 1.078
C2/C0 = 1.068
