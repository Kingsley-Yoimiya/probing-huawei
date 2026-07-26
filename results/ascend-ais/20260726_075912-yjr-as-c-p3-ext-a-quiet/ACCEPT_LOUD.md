# Acceptance (loud): P3-EXT-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.15** (cli:--min-ratio=1.15)
- recipes: `/tmp/yjr_fs_orch_20260726_075912/huawei_root/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 82.15 | 1.00 |
| C1_inject_none | 103.16 | 1.26 |
| C2_probing | 148.38 | 1.81 |

C1/C0 = 1.256
C2/C0 = 1.806
