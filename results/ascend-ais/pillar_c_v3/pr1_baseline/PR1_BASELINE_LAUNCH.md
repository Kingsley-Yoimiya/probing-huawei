# PR-1 健康长 run · 发射记录

**状态**：**DONE（recover 补完）** — dump ✅ @22:06 · 训完 ✅ 24000步 · 本机 pull ✅（recover exit 1 因 tar 截断，已手工补拉）  
**§1.5 验收**：**PARTIAL**（2026-07-28）— 见 `PR1_BASELINE.md`（5 PASS + 单 pid 体积估 PARTIAL）

---

## Run 标识

| 字段 | 值 |
|------|-----|
| run_id | `20260727_210243-yjr-as-b-pr1-health` |
| pod | `yysong-worker-0` |
| 跳板 | `ais-cf3e61a5` |
| 启动时间（CST） | 2026-07-27 ~21:06 |
| hold_exec 本机 PID | `39105`（**已 SIGTERM 143** @21:23，dump 等待中断） |
| recover 本机 | `/tmp/pr1_baseline_recover.sh`（nohup，~22:06 dump + pull） |
| torchrun 主 PID | **`2514528`** |
| victim worker（LOCAL_RANK=7） | dump 时由 `dump_probing_sql.sh` 选定 |
| 配置臂 | `C2_probing`（`INJECT_KIND=none`） |

## 配方（手册 §1.5）

| 旋钮 | 值 |
|------|-----|
| `INJECT_KIND` | `none` |
| `PROBING_TORCH_PROFILING` | `on,rate=0` |
| `PROBING_TORCH_MIN_STEP_INTERVAL` | **500** |
| `PROBING_GPU_SAMPLE_MS` / `CPU` | 500 / 500 |
| `PROBING_SPAN_BACKENDS` | `none` |
| `PILLAR_C_SET_UPGRADE` | 0 |
| `ITERS` / `WARMUP` | **24000** / **50** |
| `DUMP_WAIT_S` | **3600**（≈60min 后 dump，训程目标 ≥70min） |
| `DUMP_PROBING_SQL` | 1 |
| `ABC_CONFIGS` | `C2_probing`（仅 C2） |

## 路径

- **Pod 训练 out**（本轮实际）：`/data/yinjinrun.p-huawei/results/ascend-ais/20260727_210243-yjr-as-b-pr1-health/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/`
- **AFS 意图**（`POD_RESULTS`）：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_210243-yjr-as-b-pr1-health/`
- **本机备份**：`project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_210243-yjr-as-b-pr1-health/`
- **dump 预期**：`…/C2_probing/probing/`（pid-fix bundle 已 sync）

> 注：`hold_exec` 默认 `POD_OUT=/data/...`；训程/jsonl 在 pod `/data` 树。hold_exec 结束后 `pull_results` 会拉回本机 `pr1_baseline/<run_id>/`。

## 监控命令

```bash
# 跳板
ssh ais-cf3e61a5
K=/root/.cache/volcano/kubectl/kubectl
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml

OUT=/data/yinjinrun.p-huawei/results/ascend-ais/20260727_210243-yjr-as-b-pr1-health/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing

# 是否还在训
$K exec yysong-worker-0 -- pgrep -af 'torchrun|tbp_npu'

# 步数 / dump
$K exec yysong-worker-0 -- bash -lc "wc -l \$OUT/ranks/rank_0000.jsonl; ls \$OUT/probing 2>/dev/null | head"

# 本机 hold_exec 日志
tail -f project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_210243-yjr-as-b-pr1-health/logs/hold_exec.log
```

## 时间线（预估 CST）

| 时刻 | 事件 |
|------|------|
| ~21:06 | FIRE_OK + warmup ok (20s) |
| **~22:06** | SQL dump（`DUMP_WAIT_S=3600`） |
| ~22:14 | 训完 24000 步（step_ms≈150ms → 训程 ~68min） |

## 前置（已 DONE）

- dump 门闩 / pid-fix / global_step / trace_event 懒注册：r5b smoke PASS
- bundle `dump_probing_sql.sh`：已 sync（排除 torchrun 父进程）

## 验收待办（dump + 训完后）

1. `hold_exec.log` 含 `SQL dump attempted` + `attach=ok`
2. 写 `PR1_BASELINE.md` §1.5 六项
3. 更新 `PR1_SUMMARY.md`
4. rsync → `pr1_baseline/20260727_210243-yjr-as-b-pr1-health/`
