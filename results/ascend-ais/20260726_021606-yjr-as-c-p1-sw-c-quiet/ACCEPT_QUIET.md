# P1-SW-C spike acceptance

- window measure [100,300]
- pass if victim rank7: median≥1.15 OR p99≥1.5 OR max≥2.5
- verdict: **BITE_OK**

| rank | median | p99 | max | hit |
|---|---|---|---|---|
| rank0 | med 75.9/76.1=1.00 | p99 97.3/83.2=1.17 | max 654.9/85.0=7.71 | PASS |
| rank7 | med 76.0/76.1=1.00 | p99 325.9/319.8=1.02 | max 1063.3/407.5=2.61 | PASS |
