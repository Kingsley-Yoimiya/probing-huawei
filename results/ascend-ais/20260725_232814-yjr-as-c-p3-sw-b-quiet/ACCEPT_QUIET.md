# Acceptance (quiet): P3-SW-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `quiet`
- threshold C1/C0 ≥ **1.15** (recipes:project/probing-huawei/scripts/fail-slow/dose_recipes.yaml:P3-SW-B.quiet)
- recipes: `project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 84.83 | 1.00 |
| C1_inject_none | 178.26 | 2.10 |
| C2_probing | 178.71 | 2.11 |

C1/C0 = 2.101
C2/C0 = 2.107
