# PR-2 实验 B5d · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_141052-pillar-c-v3-pr2-e3-b5d` |
| arm | `20260728_141052-pillar-c-v3-pr2-e3-b5d-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| 前置 | B5c PASS `20260728_135724`（__file__=pydeps ext/torch.py；SET rows=4368） |
| scope | localize + **15s** 时基降回 |
| hang_max | **480s**（SET 后 step ≥8min 不动 → stop_hang） |
| 常驻 | `on,rate=0` |
| ITERS | 1000 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |

## 发射
`_prep/launch_exp_b5d.sh` @ 2026-07-28T14:11:30+08:00
