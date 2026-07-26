# Acceptance (loud): P1-SW-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `loud`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.68 | 1.00 |
| C1_inject_none | 94.63 | 1.25 |

C1/C0 = 1.250
