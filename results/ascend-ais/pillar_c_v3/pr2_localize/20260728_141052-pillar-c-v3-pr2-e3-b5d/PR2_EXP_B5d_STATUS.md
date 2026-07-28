# PR-2 实验 B5d · PARTIAL

**日期**：2026-07-28  
**parent**：`20260728_141052-pillar-c-v3-pr2-e3-b5d`

| 项 | 值 |
|----|-----|
| 头条比 | **115.05%**（v2 参考 72.6%；目标 <100%） |
| dense_ranks | **1** |
| culprit_rank | **7**（GT=7） |
| culprit TT rows | **8554** |
| 非 culprit max rows | **0** |
| SET_DOWNGRADE_OK | 1 |
| downgrade reason | time |
| 原生（非 backfill） | **yes** |
| inject_stop marker | 1 |
| ITERS 完成 | L=1000 |
| WINDOW_S | 15 |
| hang_max | 480s |
| 回拉 | tar 截断后 kubectl tar 补拉 probing_data |

- 判分：`PR2_E3_RATIO_B5d.md`
