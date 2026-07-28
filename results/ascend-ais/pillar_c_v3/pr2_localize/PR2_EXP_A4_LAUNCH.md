# PR-2 实验 A4 · 发射记录（并行有界 localize）

**状态**：**RUNNING**（foreground hold_exec）  
**case**：P3-SW-A loud · GT=rank **7** · scope=`localize` · mode=`step_ms`

---

## Run 标识

| 字段 | 值 |
|------|-----|
| run_id / parent | `20260728_095652-pillar-c-v3-pr2-localize-a4` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| 发射 | `_prep/launch_exp_a4.sh` 前台 · 禁 `\| head` |

## 相对 A3 配方差异

| 旋钮 | A3 | **A4** |
|------|-----|--------|
| ITERS | 2000 | **400**（短 pilot） |
| localize | 串行 attach-wait | **16 路并行** + total_budget=60s |
| per-pid timeout | 25s | **8s** |
| SET 块 | 无硬超时 | **timeout 120s** |
| prevalidated | 未传 | **ATTACH_READY 后 skip attach** |

## 验收目标

1. `localize.log`：`culprit_rank=7` · `LOCALIZE_ELAPSED_MS` < 60000
2. `set_upgrade.log`：1× `SET_OK_WORKER`（rank7 pid）· 非 FALLBACK
3. 无 `LOCALIZE_TIMEOUT` / `SET_FAIL_ALL`

## 路径

| 位置 | 路径 |
|------|------|
| Pod out | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/<RUN_ID>/upgrade_rate_1.0/P3-SW-A/.../C2_probing/` |
| 本机 log | `pr2_localize/_prep/logs/launch_a4_*.log` |
| 验收 | `PR2_LOCALIZE_ACC_R4.md`（跑完后） |

## 前置

- A3 `093112` 已闭环：`PR2_LOCALIZE_ACC_R3.md`
- 094730 残留 worker 由 hold_exec `clean_pod` 清场
