# Acceptance (loud): P3-EXT-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `/tmp/yjr_fs_orch_20260726_094648/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 85.31 | 1.00 |
| C1_inject_none | 125.44 | 1.47 |
| C2_probing | 162.73 | 1.91 |

C1/C0 = 1.470
C2/C0 = 1.908
