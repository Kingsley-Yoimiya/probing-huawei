# PR-2 实验 B7 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_185909-pillar-c-v3-pr2-e3-b7` |
| arm | `20260728_185909-pillar-c-v3-pr2-e3-b7-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| 前置 | B6 code(lazy comm/table + prune) 已部署 pod;B5d 头条=115.05%,预期 B7 ~40-50% |
| scope | localize + **15s** 时基降回 |
| hang_max | **480s**（SET 后 step ≥8min 不动 → stop_hang） |
| 常驻 | `on,rate=0` |
| ITERS | 1000 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |
| B6 gates | COMM_LAZY=1 · STEP_TIMING_LAZY=0 · PRUNE_EXTRA_PIDS=1 · DRY=0 |

## 发射
`_prep/launch_exp_b7.sh` @ 2026-07-28T18:59:09+08:00
