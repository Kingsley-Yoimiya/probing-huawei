# Acceptance (loud): P1-HW-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_005203/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.88 | 1.00 |
| C1_inject_none | 92.50 | 1.22 |
| C2_probing | 94.26 | 1.24 |

C1/C0 = 1.219
C2/C0 = 1.242
