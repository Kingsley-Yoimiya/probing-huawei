# PR-2 实验 A6 · 发射记录（pid 选择对齐 dump）

**状态**：**DONE** · `culprit_rank=7` · `LOCALIZE_FALLBACK=0` · SET pid=**1459716**
**case**：P3-SW-A loud · GT=rank **7** · scope=`localize` · mode=`step_ms`

---

## Run 标识

| 字段 | 值 |
|------|-----|
| run_id / parent | `20260728_102830-pillar-c-v3-pr2-localize-a6` |
| arm_run_id | `20260728_102830-pillar-c-v3-pr2-localize-a6-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| 发射 | `_prep/launch_exp_a6.sh` · 禁 `\| head` |

## 相对 A5 修复

| 项 | A5 | **A6** |
|----|-----|--------|
| pid 选择 | attach ping + min pid | **dump 同分：shm torch_trace + 每 rank 一 pid** |
| raw_head | 200 字符 | **2000** |
| FALLBACK cands | 48 pid 裸 ps | **`--list-worker-pids` 过滤（≤16）** |
| SQL | step_duration_sec ✅ | 同左 |
| pytest | 11 | **13 passed** |

## 验收目标

1. `localize.log`：`culprit_rank=7` · pid 与 dump rank7 一致 · `fallback=False`
2. `set_upgrade.log`：`LOCALIZE_FALLBACK=0` · **1×** `SET_OK_WORKER`
3. `CANDS_FALLBACK` 若出现应 ≤16 pid

## 前置

- A5 `101600` FAIL 见 `PR2_LOCALIZE_ACC_R5.md`
- 已杀 `102530` / `101600` 重复 launch 链

## 路径

Pod out：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_102830-…/upgrade_rate_1.0/P3-SW-A/…/C2_probing/`  
本机 log：`pr2_localize/_prep/logs/launch_a6_20260728_102830.log`
