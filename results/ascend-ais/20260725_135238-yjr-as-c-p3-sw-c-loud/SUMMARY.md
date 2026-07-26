# SUMMARY — P3-SW-C Loud `20260725_135238-yjr-as-c-p3-sw-c-loud`

- case: P3-SW-C（监控自身泄漏）inject `8c` sidecar
- dose: `cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0,max_chunks=64`（calibrated）
- mode: host_bound；pod=`yysong-master-0`；world=16
- C0/C1/C2: 齐；C1 由 pod-local supervisor 在 step100 准时注入（`supervisor.log`）
- Loud: C1/C0_step_ms=**2.33** PASS（≥1.3）
- Score: offline **D3**；SQL **D4**（PASS_D4）
- 对照: CONTRAST_QUEUE GH+XPU 已 DONE（不重开）
- 备注: 原 Case 代理 timeout；收尾接管 hold_exec/`134034` 失败臂后改用 `135238`；未碰 P3-SW-B`125558` / P2-SW-C`124102`
