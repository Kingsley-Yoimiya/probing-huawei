# PR-2 实验 B2 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_111524-pillar-c-v3-pr2-e3-b2` |
| arm | `20260728_111524-pillar-c-v3-pr2-e3-b2-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| scope | localize + B2 短窗 `12` step |
| ITERS | 1800 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |
| ETA | ~35–55 min（过 inject_stop=300） |

## B2 变更
- SET_UPGRADE @ localize culprit → rate=1.0
- SET_DOWNGRADE @ L+12 → rate=0（同 pid）
- hang_max=900s step 不动 → 停训留证

## 发射
`_prep/launch_exp_b2.sh` @ 2026-07-28T11:15:24+08:00
