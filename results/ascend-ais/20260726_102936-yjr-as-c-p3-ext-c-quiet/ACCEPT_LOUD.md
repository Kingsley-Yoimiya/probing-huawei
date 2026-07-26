# Acceptance (loud): P3-EXT-C

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_102936/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 83.21 | 1.00 |
| C1_inject_none | 158.58 | 1.91 |
| C2_probing | 90.61 | 1.09 |

C1/C0 = 1.906
C2/C0 = 1.089
