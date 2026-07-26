# Acceptance (loud): P1-SW-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_052307/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.98 | 1.00 |
| C1_inject_none | 97.83 | 1.29 |
| C2_probing | 99.86 | 1.31 |

C1/C0 = 1.288
C2/C0 = 1.314
