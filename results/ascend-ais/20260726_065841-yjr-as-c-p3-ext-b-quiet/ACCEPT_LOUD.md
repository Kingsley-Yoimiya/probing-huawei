# Acceptance (quiet): P3-EXT-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `quiet`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_065841/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 106.19 | 1.00 |
| C1_inject_none | 181.50 | 1.71 |
| C2_probing | 147.31 | 1.39 |

C1/C0 = 1.709
C2/C0 = 1.387
