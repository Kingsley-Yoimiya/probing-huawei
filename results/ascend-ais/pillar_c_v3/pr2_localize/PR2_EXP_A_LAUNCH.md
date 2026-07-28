# PR-2 实验 A · 发射记录

**状态**：**DONE · FAIL**（SET/dump 已完成；本机 hold_exec 被 `| head` 提前断开；训程 orphan 自行跑完）  
**case**：P3-SW-A loud · GT=rank **7** · scope=`localize`

---

## Run 标识

| 字段 | 值 |
|------|-----|
| run_id / parent | `20260728_084329-pillar-c-v3-pr2-localize-a` |
| arm_run_id | `20260728_084329-pillar-c-v3-pr2-localize-a-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| kube | `/tmp/config-vc-a3-241ceshi-songyiyang.yaml`（SYY） |
| 启动时间（CST） | **2026-07-28 ~08:43** |
| hold_exec 本机 PID | **84246**（`hold_exec_run_case.sh`；父 `run_pillar_c_arm.sh` 84230） |
| torchrun 主 PID（pod） | **52683** |

## 配方（手册 §2.4 实验 A）

| 旋钮 | 值 |
|------|-----|
| `ARM` | `e3a_upgrade` |
| `CASE_ID` / `DOSE` | P3-SW-A / loud |
| `SIDECAR_LOCAL_RANK` | **7**（GT） |
| `PILLAR_C_SET_SCOPE` | **localize** |
| `PILLAR_C_SET_AT_STEP` | 100 |
| `PILLAR_C_SET_RATE` | 1.0 |
| `RESIDENT_RATE` | 0 |
| `ITERS` / `WARMUP` | **2000** / **50** |
| `DUMP_WAIT_S` | **180**（< 训程；SET 后 ~3min dump） |
| `INJECT_KIND` | 8a（inline GC stall） |
| SET 键 | `probing.torch.profiling=` |

## 路径

| 位置 | 路径 |
|------|------|
| Pod out（AFS） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_084329-pillar-c-v3-pr2-localize-a/upgrade_rate_1.0/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/` |
| probing_data | `…/upgrade_rate_1.0/probing_data` |
| 本机（hold_exec 工作区） | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/pillar_c/20260728_084329-pillar-c-v3-pr2-localize-a/upgrade_rate_1.0/` |
| 发射脚本 | `pr2_localize/_prep/launch_exp_a.sh` |

> 注：`run_pillar_c_arm.sh` 将 `OUT_FAMILY` 默认回落为 `pillar_c`（`:-` 对空串生效），AFS 落 `ascend-ais/pillar_c/<run_id>/` 而非 `pillar_c_v3/pr2_localize/`；本机备份仍在 `pr2_localize/pillar_c/…`。

## 时间线（预估 CST）

| 时刻 | 事件 |
|------|------|
| ~08:43 | FIRE_OK · torchrun 16 rank |
| ~08:45:32 | L=138 ≥ 100 → **localize + SET**（见下方早读） |
| ~08:48:36 | SQL dump（SET + 180s） |
| ~08:55–09:05 | 训完 2000 步（host_bound + 8a 注入，~0.3–0.5s/step） |

## 早读（SET 瞬间，待终态验收）

`localize.log` @08:45:36：

- `LOCALIZE_SQL: culprit_rank=0`（**≠ GT 7**）
- 全 rank `comm_max` metric **均为 0.0**（`python.comm_collective` 窗内无行）
- `LOCALIZE_FALLBACK=0`（未走全 rank fallback；tie-break 取 rank 0）
- `set_upgrade.log`：仅 **pid=53091（local_rank=0）** `SET_OK_WORKER`

→ 预判验收 **#1 FAIL**；Loop 终态后写 `PR2_LOCALIZE_ACC.md`。

## 监控

```bash
# 跳板
ssh ais-cf3e61a5
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
K=/root/.cache/volcano/kubectl/kubectl
OUT=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_084329-pillar-c-v3-pr2-localize-a/upgrade_rate_1.0/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing

$K exec yysong-worker-0 -- wc -l $OUT/ranks/rank_0000.jsonl
$K exec yysong-worker-0 -- tail -30 $OUT/set_upgrade.log
$K exec yysong-worker-0 -- ls $OUT/probing 2>/dev/null | head

# 本机 hold_exec（若仍在前台/nohup）
ps -p 84230 -o etime,command
```

## 验收待办（训完 + pull 后）

1. `localize.log`：`culprit_rank==7`？
2. `set_upgrade.log`：仅 rank 7 pid SET？
3. dump 后 rank7 `torch_trace` rows>0，其余稀采？
4. 写 `PR2_LOCALIZE_ACC.md`；rsync → `pr2_localize/<run_id>/`
