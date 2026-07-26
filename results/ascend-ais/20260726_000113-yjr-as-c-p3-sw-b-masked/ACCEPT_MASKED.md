# Acceptance (masked): P3-SW-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `masked`
- threshold C1/C0 ≥ **1.05** (recipes:project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P3-SW-B.masked)
- recipes: `project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 92.69 | 1.00 |
| C1_inject_none | 176.98 | 1.91 |
| C2_probing | 180.51 | 1.95 |

C1/C0 = 1.909
C2/C0 = 1.947
