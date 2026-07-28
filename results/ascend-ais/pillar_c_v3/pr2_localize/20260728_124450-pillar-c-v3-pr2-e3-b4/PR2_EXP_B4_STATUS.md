# PR-2 实验 B4 · PARTIAL

**日期**：2026-07-28  
**parent**：`20260728_124450-pillar-c-v3-pr2-e3-b4`

| 项 | 值 |
|----|-----|
| 头条比 | **133.72%**（v2 参考 72.6%；门槛 ≤80%） |
| dense_ranks | **16** |
| culprit_rank | **7**（GT=7） |
| SET_DOWNGRADE_OK | **1** |
| downgrade reason | **time** |
| 原生（非 backfill） | **yes**（AFS 含 `probing -t` unix socket 输出；无 `mac_triggered_backfill`） |
| inject_stop marker | 1 |
| node_0.done | ✅ |
| rank_0007.jsonl | 1800 行 |
| 窗口 | 30s / steps=0 |

## B4 相对 R3

| 维度 | R3 | B4 |
|------|----|----|
| SET_DOWNGRADE 执行 | Mac 触发 + AFS 补写 | **pod 内阻塞 jexec** ✅ |
| SET_DOWNGRADE_OK | backfill | **原生** ✅ |
| jexec_poll (Mac) | 失败 | **python timeout** ✅ |
| dense | 16 | 16（未改善） |
| headline | 133.72% | 133.72%（未改善） |

## 根因分析（dense 仍 16）

- 降回 @ L=229（elapsed 38s）已执行 `probing config rate=0` 并写 `SET_DOWNGRADE_OK`
- 但 **16 路** `python.torch_trace` 均有 729 rows / 9 steps（各 ~20MB）——resident `on,rate=0` 基线已在全 rank 落 trace
- 仅 culprit 升详 30s 后降回，**不能回收** 已写入的 trace；需后续 PR 改采集策略或判分窗口

- 判分：`PR2_E3_RATIO_B4.md`
- 发射：`PR2_EXP_B4_LAUNCH.md`
