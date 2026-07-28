# PR-2 实验 B8 · 长跑发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_204936-pillar-c-v3-pr2-e3-b8` |
| arm | `20260728_204936-pillar-c-v3-pr2-e3-b8-upgrade_rate_1.0` |
| pod | `grj-megatron-32card-0716-worker-0`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P3-SW-A · GT culprit rank=7 |
| ITERS | 1000 · inject [100,300] |
| scope | localize + **15s** 时基降回 |
| hang_max | **480s**（8min） |
| 常驻 | `on,rate=0` |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |
| B8 gates | STEP_AGG=**avg** STEP_WINDOW=**100** NO_PROG_KILL_S=**90** HCCL_EXEC_TIMEOUT=**600** |
| B6 gates | COMM_LAZY=1 · STEP_TIMING_LAZY=0 · PRUNE=1 · DRY=0 |
| 前置 smoke | `20260728_203149-pillar-c-v3-pr2-e3-b8-smoke` PASS ✓ |
| 目标 | headline<100% · culprit=7 · dense=1 · LOCALIZE_FALLBACK=0 · SET_OK+DG=Y |

## 发射
`_prep/launch_exp_b8.sh` @ 2026-07-28T20:49:36+08:00
