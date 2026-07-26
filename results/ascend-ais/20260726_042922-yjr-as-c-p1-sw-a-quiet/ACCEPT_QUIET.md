# Acceptance (quiet): P1-SW-A

- window: measure step [100, 300] rank0 `step_ms` median
- dose: `quiet`
- threshold C1/C0 ≥ **1.15** (recipes:scripts/fail-slow/dose_recipes.yaml:P1-SW-A.quiet)
- recipes: `scripts/fail-slow/dose_recipes.yaml`
- injection.log: `no_log`
- verdict: **PASS**

| config | median step_ms | vs C0 |
|---|---:|---:|
| C0_baseline | 75.98 | 1.00 |
| C1_inject_none | 124.44 | 1.64 |
| C2_probing | 126.58 | 1.67 |

C1/C0 = 1.638
C2/C0 = 1.666
