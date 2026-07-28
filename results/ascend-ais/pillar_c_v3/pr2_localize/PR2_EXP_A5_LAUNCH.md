# PR-2 实验 A5 · 发射记录（step_duration_sec 列名修复）

**状态**：**RUNNING**（background hold_exec）  
**case**：P3-SW-A loud · GT=rank **7** · scope=`localize` · mode=`step_ms`

---

## Run 标识

| 字段 | 值 |
|------|-----|
| run_id / parent | `20260728_100857-pillar-c-v3-pr2-localize-a5` |
| arm_run_id | `20260728_100857-pillar-c-v3-pr2-localize-a5-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| 发射 | `_prep/launch_exp_a5.sh` · 禁 `\| head` |

## 相对 A4 配方差异

| 旋钮 | A4 | **A5** |
|------|-----|--------|
| SQL 列 | `max(step_ms)` ❌ | **`max(step_duration_sec)`** ✅ |
| ITERS | 400 | **1800** |
| DUMP_WAIT_S | 60 | **90**（仍 < 训程） |
| localize 并行/预算 | 16 路 · 60s | 同 A4 |
| secondary | host_rss | 保留 |

## 代码修复（A5 前置）

| 文件 | 改动 |
|------|------|
| `pillar_c_localize_culprit.py` | `step_ms` 模板 → `max(step_duration_sec)`；失败行写 `raw_head`；metric 全 0 → `metric_zero_flat` + secondary |
| `test_pillar_c_localize_culprit.py` | 断言 `step_duration_sec`；11/11 PASS |
| probe-bundle | 预同步 `pillar_c_localize_culprit.py`（jsync） |

## 验收目标

1. `localize.log`：`mode=step_ms` · **`culprit_rank=7`** · `fallback=False`
2. `set_upgrade.log`：1× `SET_OK_WORKER`（rank7 pid）· **`LOCALIZE_FALLBACK=0`**
3. 非 None / 非 rank0 tie-break

## 路径

| 位置 | 路径 |
|------|------|
| Pod out | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/<RUN_ID>/upgrade_rate_1.0/P3-SW-A/.../C2_probing/` |
| 本机 log | `pr2_localize/_prep/logs/launch_a5_*.log` |
| 验收 | `PR2_LOCALIZE_ACC_R5.md`（跑完后） |

## 前置

- A4 `095652` 根因：`step_ms` 列名错误 → DataFusion Schema error → ok=False 全 rank → FALLBACK rank0
- A5 发射前 yysong-w0 **IDLE**（0 train worker）
