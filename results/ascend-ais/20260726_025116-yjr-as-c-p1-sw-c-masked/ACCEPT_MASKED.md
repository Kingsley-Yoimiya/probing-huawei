# P1-SW-C spike acceptance

- window measure [100,300]
- pass if victim rank7: median≥1.05 OR p99≥1.5 OR max≥2.5
- verdict: **BITE_OK**

| rank | median | p99 | max | hit |
|---|---|---|---|---|
| rank0 | med 75.9/76.2=1.00 | p99 82.6/80.0=1.03 | max 762.2/91.3=8.35 | PASS |
| rank7 | med 76.0/75.9=1.00 | p99 316.1/325.4=0.97 | max 1070.4/410.2=2.61 | PASS |
