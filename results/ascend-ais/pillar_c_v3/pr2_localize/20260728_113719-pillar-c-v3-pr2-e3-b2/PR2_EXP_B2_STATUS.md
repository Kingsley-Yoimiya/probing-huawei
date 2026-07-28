# PR-2 实验 B2 · **BLOCKED**

**日期**：2026-07-28  
**parent**：`20260728_113719-pillar-c-v3-pr2-e3-b2`（R5）

| 项 | 值 |
|----|-----|
| 头条比 | **BLOCKED**（raw 0.0% · 无 probing_data · 不可比） |
| dense_ranks | **0** |
| culprit_rank (SQL) | **9**（GT=**7** · 误指） |
| LOCALIZE_FALLBACK | 0 |
| SET_UPGRADE | ✅ L=133 · pid=2027766 · rate=1.0 |
| SET_DOWNGRADE | ❌ **0**（窗降回未生效） |
| HANG_DETECTED | ✅ L=137 · stall≥900s · 11:54:21 |
| inject_stop marker | ❌ |
| pod | **IDLE**（11:54 手工 pkill 后） |

## 根因

1. **SET rate=1.0 后 collective stall**：全 rank jsonl 卡 **137 行**，mtime **11:38:58** 起 ≥14min 无进展（同 B@122，略推迟）。
2. **B2 短窗降回未跑**：Mac 侧 `hold_exec` 卡在 SET 后 `jexec` 块，B2 wait/downgrade 逻辑未执行；L=137 < downgrade_at=**145**（culprit rank9 jsonl）。
3. **SQL 误指**：localize 判 culprit=**9** ≠ GT victim **7**；SET 打在 rank9 pid。

## 证据

- `dynamic/set_upgrade.log`：`HANG_DETECTED` + `B2_HANG_STOP`
- `logs/babysit_run.log` · `logs/arm_dynamic.log`
- 判分：`PR2_E3_RATIO_B2.md` / `.json`

## 后续

- 修 hold_exec：SET 后 B2 轮询与主流程解耦 / 跳板侧 babysit
- 修 babysit：`pre_stall` 首次 poll 不再清零（`_prep/babysit_b2_r5.sh` 已修）
- 复测需新 run_id（本 run 终态 BLOCKED）
