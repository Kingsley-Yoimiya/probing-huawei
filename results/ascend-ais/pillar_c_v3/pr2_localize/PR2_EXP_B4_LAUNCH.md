# PR-2 实验 B4 · 发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_124450-pillar-c-v3-pr2-e3-b4` |
| arm | `20260728_124450-pillar-c-v3-pr2-e3-b4-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| case | P3-SW-A · GT culprit rank=7 |
| scope | localize + B4 时基 `30`s（steps=`0`） |
| ITERS | 1800 · inject [100,300] |
| 全量臂 | REUSE v2 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity` |
| ETA | ~35–55 min（过 inject_stop=300） |

## B4 目标（相对 B3 R3）
- SET_DOWNGRADE 在 **pod 内** `probing -t $pid config rate=0` 执行
- `set_upgrade.log` **当场**写 `SET_DOWNGRADE_OK reason=time`（非 backfill）
- Mac 无 GNU `timeout` 时 `jexec_poll` 仍成功（python subprocess）
- 降回用阻塞 `jexec`（非 poll）

## 发射
`_prep/launch_exp_b4.sh` @ 2026-07-28T12:44:50+08:00
