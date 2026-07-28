# PR-2 实验 B5b · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_132612-pillar-c-v3-pr2-e3-b5b` |
| arm | `20260728_132612-pillar-c-v3-pr2-e3-b5b-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| B5b 改动 | `torch_probe` rate=0 零行 + `ext/torch.py` C0 热更直写 tracer |
| scope | localize + 30s 时基降回 |
| ITERS | 1800 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |

## 发射
`_prep/launch_exp_b5b.sh` @ 2026-07-28T13:40:01+08:00
