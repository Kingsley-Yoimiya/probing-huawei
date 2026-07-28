# PR-2 实验 A2 · 发射记录（修复后复跑）

**状态**：**RUNNING**（hold_exec 前台监督中；SET 已完成）  
**case**：P3-SW-A loud · GT=rank **7** · scope=`localize` · mode=**`step_ms`**

---

## Run 标识（有效轮 · 091413）

| 字段 | 值 |
|------|-----|
| run_id / parent | `20260728_091413-pillar-c-v3-pr2-localize-a2` |
| arm_run_id | `20260728_091413-pillar-c-v3-pr2-localize-a2-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| 启动时间（CST） | **2026-07-28 ~09:14** |
| 发射方式 | **前台 hold_exec**（`launch_exp_a2.sh`；禁 `\| head`） |
| torchrun 主 PID（pod） | **358648** 系（16 worker；master torchrun 见 node_0.log） |

## 废弃轮（未起训 / orphan）

| run_id | 问题 |
|--------|------|
| `20260728_085327-…-a2` | jsync 后 hold_exec 早退，未 FIRE |
| `20260728_090358-…-a2` | FIRE_OK 后 hold_exec orphan，无 SET |
| `20260728_091011-…-a2` | 同上 orphan |
| `20260728_091248-…-a2` | BUSY 清场后 FIRE 但 hold_exec orphan |

## 配方

| 旋钮 | 值 |
|------|-----|
| `PILLAR_C_LOCALIZE_MODE` | **`step_ms`**（显式；非 comm_max） |
| `PILLAR_C_SET_AT_STEP` | 100 |
| `PILLAR_C_SET_SCOPE` | localize |
| `SIDECAR_LOCAL_RANK` | 7 |
| `ITERS` / `DUMP_WAIT_S` | 2000 / 180 |

## 时间线（CST · 091413）

| 时刻 | 事件 |
|------|------|
| ~09:14 | jsync bundle + **FIRE_OK** |
| ~09:14:20 | warmup ok (20s) |
| ~09:14:30 | inject@step100 · 8a GC stall victim=7 |
| ~09:14:30 | L=131 → **localize + SET** |
| ~09:16:19 | SET_END（见 localize 结果） |
| ~09:17+ | dump@180s · 训程继续 |

## 路径

| 位置 | 路径 |
|------|------|
| Pod out（AFS） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_091413-pillar-c-v3-pr2-localize-a2/upgrade_rate_1.0/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/` |
| 本机 log | `pr2_localize/_prep/logs/launch_a2_20260728_091413.log` |
| 发射脚本 | `pr2_localize/_prep/launch_exp_a2.sh` · `fire_a2.sh` |

## SET 早读

见 `PR2_LOCALIZE_ACC_R2.md`：`culprit_rank=None` · FALLBACK 全 rank SET。
