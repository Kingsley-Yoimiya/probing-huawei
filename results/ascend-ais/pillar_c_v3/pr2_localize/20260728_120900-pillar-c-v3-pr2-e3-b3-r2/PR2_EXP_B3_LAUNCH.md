# PR-2 实验 B3 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_120900-pillar-c-v3-pr2-e3-b3-r2` |
| arm | `20260728_120900-pillar-c-v3-pr2-e3-b3-r2-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| scope | localize + B3 时基 `30`s（steps=`0`） |
| ITERS | 1800 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |
| ETA | ~35–55 min（过 inject_stop=300） |

## B3 变更
- SET_UPGRADE @ localize culprit → rate=1.0
- SET_DOWNGRADE @ **时间** window_s=30s（或可选步数）→ rate=0（同 pid）
- jexec_poll 超时=25s；hang_max=900s

## 发射
`_prep/launch_exp_b3.sh` @ 2026-07-28T12:08:43+08:00
