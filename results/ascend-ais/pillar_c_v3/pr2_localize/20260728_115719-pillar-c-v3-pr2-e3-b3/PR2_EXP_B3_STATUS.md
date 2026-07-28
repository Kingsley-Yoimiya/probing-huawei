# PR-2 实验 B3 · **PARTIAL**（R1 作废）

**parent**：`20260728_115719-pillar-c-v3-pr2-e3-b3`  
**勿判 DONE**

| 项 | 值 |
|----|-----|
| SET_UPGRADE | ✅ @ L=134 pid=2496910 rate=1.0 |
| SET_DOWNGRADE | ❌ **0**（Mac 侧 `B3 skip: no SET_OK pid`） |
| culprit_rank | **12** ≠ GT=7 |
| inject_stop | ✅ step_300 |
| 训程 | ✅ done (100s) |

## 根因
SET jexec 写 log 成功后，Mac 侧 grep/sed 读回 `SET_OK_WORKER pid=` 空 → 时基降回整段 skip。

## 下一
R2 `20260728_121500-pillar-c-v3-pr2-e3-b3-r2`：read_set_upgrade_field + 空读重试 + victim=7 fallback。
