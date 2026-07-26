# Acceptance (loud): P1-EXT-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `/tmp/yjr_fs_orch_20260726_014611/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.52 | 1.00 |
| C1_inject_none | 81.38 | 1.08 |
| C2_probing | 82.03 | 1.09 |

C1/C0 = 1.078
C2/C0 = 1.086
