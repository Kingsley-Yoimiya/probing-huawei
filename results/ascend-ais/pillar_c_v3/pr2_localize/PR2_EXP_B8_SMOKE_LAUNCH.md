# PR-2 实验 B8 · smoke 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_203149-pillar-c-v3-pr2-e3-b8-smoke` |
| arm | `20260728_203149-pillar-c-v3-pr2-e3-b8-smoke-upgrade_rate_1.0` |
| pod | `grj-megatron-32card-0716-worker-0`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P3-SW-A · GT culprit rank=7 |
| ITERS | 200 · inject [100,180] |
| scope | localize + 15s 时基降回 |
| hang_max | **180s** |
| B8 gates | STEP_AGG=**avg** STEP_WINDOW=**100** NO_PROG_KILL_S=**90** HCCL_EXEC_TIMEOUT=**600** |
| B6 gates | COMM_LAZY=1 · STEP_TIMING_LAZY=0 · PRUNE=1 · DRY=0 |

## 发射
`_prep/launch_exp_b8_smoke.sh` @ 2026-07-28T20:31:49+08:00
