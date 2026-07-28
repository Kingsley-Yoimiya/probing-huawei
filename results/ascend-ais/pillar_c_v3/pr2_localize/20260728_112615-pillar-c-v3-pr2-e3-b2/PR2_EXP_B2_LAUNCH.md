# PR-2 实验 B2 · 发射记录（R2）

| 字段 | 值 |
|------|-----|
| parent | `20260728_112615-pillar-c-v3-pr2-e3-b2` |
| arm | `20260728_112615-pillar-c-v3-pr2-e3-b2-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 废弃 | `20260728_111524-pillar-c-v3-pr2-e3-b2`（jsync 卡死） |
| B2 窗口 | `12` step 后 SET_DOWNGRADE rate=0 |
| jsync | `HOLD_EXEC_SKIP_HEAVY_JSYNC=1`（bundle 已有 train/sidecar） |
| 全量臂 | REUSE v2 `20260725_230350-…/full_fidelity` |
| ETA | ~35–55 min（过 inject_stop=300） |

发射：`_prep/launch_exp_b2_r2.sh` @ 2026-07-28T11:26:15+08:00
