# Acceptance (loud): P3-SW-C

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_125953/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 83.57 | 1.00 |
| C1_inject_none | 163.29 | 1.95 |
| C2_probing | 87.79 | 1.05 |

C1/C0 = 1.954
C2/C0 = 1.050
