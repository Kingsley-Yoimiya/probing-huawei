# PR-2 实验 B3 · **PARTIAL**

**日期**：2026-07-28  
**parent**：`20260728_122300-pillar-c-v3-pr2-e3-b3-r3`（R3 · 权威 run）

| 项 | 值 |
|----|-----|
| 头条比 | **133.72%**（v2 参考 72.6%） |
| dense_ranks | **16**（期望 1） |
| culprit_rank | **7** ✅（GT=7） |
| SET_UPGRADE | ✅ @ L=127 pid=2908665 rate=1.0 |
| SET_DOWNGRADE | ✅ `reason=time` window_s=30（Mac 触发；AFS 补写 `mac_triggered_backfill=1`） |
| inject_stop | ✅ step_300 · rank7 jsonl **1800** 行 |
| node_0.done | ✅ |

## 备注
- R4 `123100` 已 kill（勿用）；R3 训程完整。
- 降回 Mac 侧 jexec 在 `timeout` 缺失时失败 → 升详窗内实际未在 pod 及时 rate=0 → dense 仍 16 路。
- 代码已修：`read_set_upgrade_field` + victim fallback + `jexec_poll` python timeout + 降回改阻塞 `jexec`。

- 判分：`PR2_E3_RATIO_B3.md`
- 发射：`PR2_EXP_B3_LAUNCH.md`（R3 对应 `_prep/logs/20260728_122300-…`）
