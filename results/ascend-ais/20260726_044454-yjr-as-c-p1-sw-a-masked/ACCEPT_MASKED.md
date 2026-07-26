# Acceptance (masked): P1-SW-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `masked`
- threshold C1/C0 ≥ **1.05** (recipes:scripts/fail-slow/dose_recipes.yaml:P1-SW-A.masked)
- recipes: `scripts/fail-slow/dose_recipes.yaml`
- injection.log: `started`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.65 | 1.00 |
| C1_inject_none | 95.22 | 1.26 |
| C2_probing | 95.50 | 1.26 |

C1/C0 = 1.259
C2/C0 = 1.262
