# Acceptance (masked): P1-HW-B

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `masked`
- threshold C1/C0 ≥ **1.05** (cli:--min-ratio=1.05)
- recipes: `project/probing-huawei/scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 76.01 | 1.00 |
| C1_inject_none | 85.81 | 1.13 |
| C2_probing | 88.29 | 1.16 |

C1/C0 = 1.129
C2/C0 = 1.162

