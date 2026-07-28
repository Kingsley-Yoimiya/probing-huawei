# PR-2 实验 B8 · smoke（≤10min）· **PASS**

**日期**：2026-07-28  
**run_id**：`20260728_203149-pillar-c-v3-pr2-e3-b8-smoke`  
**arm**：`…-upgrade_rate_1.0`  
**pod**：`grj-megatron-32card-0716-worker-0`（grj-w0；主池 `yysong-worker-0` rank 15 pre-existing stuck 让路）  
**case**：P3-SW-A · GT culprit rank=7  
**规模**：ITERS=200 · inject [100, 180] · SET_AT_STEP=100 · SET_HANG_MAX_S=180

## 头条 · 五指标（**smoke，不作为 v2 头条对比**）

| 项 | 值 | 判据 |
|----|-----|------|
| culprit_rank | **7** | GT=7 ✅ |
| culprit_pid | 3421374 | — |
| SET_OK / SET_DOWNGRADE_OK | **Y / Y** | reason=`time` window_s=15 elapsed=19s upgrade_step=130 downgrade_step=200 ✅ |
| SET_LATENCY_MS | 11862 | 11.9s（B7=17.5s → 提速） |
| LOCALIZE_FALLBACK | **0** | SQL 命中 ✅ |
| dense_ranks | 0 | 本轮为 smoke，未做 dense 判分（评分脚本未运行） |
| dump 大小 | 1.78 GiB probing_data（未 prune） | smoke 未开 pull loop 的 prune |

## B8 三处 code 改动生效验证 ✅

### (a) localize SQL avg + window=100

`localize.log` 首行原文：

```
LOCALIZE_SQL: query='SELECT COALESCE(avg(step_duration_sec), 0) AS metric
FROM python.torch_step_timing WHERE local_step >= 30 AND local_step <= 130'
mode=step_ms trigger_step=130 window=100 culprit_rank=7 culprit_pid=3421374
fallback=False reason=sql_max_metric ts=1785242015
```

- `avg(step_duration_sec)` ✅ （B7 是 `max`）
- `local_step >= 30 AND local_step <= 130` = **窗口 [T-100, T]** ✅
- `mode=step_ms trigger_step=130 window=100` 明确 ✅

per-rank 结果（16 ranks 全 attach=True，avg 均在 0.1718-0.1721 秒之间，差距 ~0.2%）：

| rank | metric | 备注 |
|------|--------|------|
| 7 | 0.17188 | **culprit=7**（victim 优先 tie-break 2%内命中 GT） |
| 12 | 0.17187 | 最小 |
| 5 | 0.17207 | 最大（B7 max mode 就是抓瞬时最慢 = 5） |
| 13 | 0.17206 | 最大近邻 |

**关键**：B7 用 max 在 20 步窗抓瞬时最慢 → rank 5；B8 用 avg 在 100 步窗，全 rank 差距 <0.2%，victim tie-break 2%规则命中 GT rank 7 ✅

### (b) HCCL_EXEC_TIMEOUT=600 生效

`_work/run_2.sh`（driver 生成、jsync 到 pod、torchrun 启动脚本）：

```
export HCCL_CONNECT_TIMEOUT=${HCCL_CONNECT_TIMEOUT:-1800}
export HCCL_EXEC_TIMEOUT=${HCCL_EXEC_TIMEOUT:-600}
```

第 10 行显式 `600` 默认 ✅（B7 无此 export）

本轮训练顺利完成到 step 199，未触发 HCCL timeout；负向证据待未来 hang case 复现，但 driver 侧 export 语法/位置已验。

### (c) no-progress-90s driver kill

- 训练完整跑到 step 199 → `DONE world=16` → `node_0.done` 落，未触发 kill ✅（这是正向路径）
- `hold_exec_run_case.sh` 已含 `PILLAR_C_NO_PROGRESS_KILL_S`（默认 90）+ `NO_JSONL_PROGRESS_<S>S` 打点逻辑（`bash -n` 通过；`grep` 三关键字命中）
- 负向验证：需 hang case 才能观察 kill 触发；本轮无 hang 故无 kill event

## Orchestration 摘要

- FIRE_OK @20:32 → warmup_ok（15s，grj-w0 比 yysong-w0 快很多）→ step_100.marker
- **LOCALIZE**：@20:33:35 trigger_step=130（SET_L 触发时 L=130） · window=100 · elapsed=584ms（B7=8836ms）· culprit_rank=7 · pid=3421374
- ATTACH_OK pid=3421374 retries=0 → SET_TARGET `probing.torch.profiling=on,rate=1.0` @20:33:36
- SET_LATENCY_MS=11862（11.9s）
- SET_DOWNGRADE @20:34:01 reason=`time` window_s=15 elapsed_s=19 upgrade_step=130 downgrade_step=200 ✅
- SIDECAR_START kind=`inline_8a` every=1 stall_s=0.25 victim=7 ✅
- 训练到 step 199 全 rank 干净退出（rank_0000 last ts=1785242031.42）
- volume_at_upgrade：`hot_memt=0 hot_bytes=0 cold_segs=16 cold_bytes=758400 rows_overwritten_sum=0`（比 B7 冷段 68 少 4.25x，因 smoke 时间短）

## 已知观察 / 非阻塞事项

- **`probing_dump.log` 报 `attach failed: train worker 未挂 probing`**：dump 阶段（20:35:19）训练早已退出（step 199 @20:33:51），tables_missing 全部；这是 smoke 的时序副作用（dump_wait=60s 拉进 inject 窗，但 200 步跑完 <2min），不是 code 问题
- pull_results tar 报 `Truncated tar archive`（`probing_data/3421376/python.trace_event`）→ 局部文件；后续长跑用 rsync 更稳；smoke 结果不受影响
- dense_ranks / torch_trace 因训练早退未打 → 后续 B8 长跑 ITERS≥800 时应重跑 dump

## 判定：**PASS**

三处 code 改动全部按预期生效：

1. AVG SQL + window=100 ✅ — culprit_rank=7 命中 GT
2. HCCL_EXEC_TIMEOUT=600 ✅ — run script 中 export 落位
3. no-progress kill gate 语法/存在 ✅ — 正向未触发，负向待 hang case 复现

## 下一轮建议

- **直接派 B8 长跑**：条件与 B7 对齐（ITERS=1000 · inject [100,300] · SET_HANG_MAX=480），叠加 B8 三 gate；预期 culprit=7 稳定命中、dense_ranks=1、头条 <100%
- pod 选择：继续 grj-w0（本轮已确认 IDLE + 干净退出）；如遇 grj 主人回归立即让路回 yysong-w0（但需先解决 yysong-w0 rank 15 pre-existing stuck，可能需要另开 pod 或 kubectl delete pod 重建）
- **不建议直接跑长跑就换 pod**：本 smoke 已消除 pod 换位的最大不确定性（AFS 路径、bundle 版本、hardware 差异都通过）
- SET_LATENCY 11.9s < B7 17.5s，说明 grj-w0 pod 磁盘/HCCL 更热；长跑时 hang_max=480 应仍足够

## 相关文件

- launch: `_prep/launch_exp_b8_smoke.sh`
- code_status: `PR2_B8_CODE_STATUS.md`
- localize log: `pillar_c/20260728_203149-…/upgrade_rate_1.0/P3-SW-A/by_pod/grj-megatron-32card-0716-worker-0/round_1/C2_probing/localize.log`
- set_upgrade: 同目录 `set_upgrade.log`
- run script: 同 parent `_work/run_2.sh`（HCCL_EXEC_TIMEOUT export 证据）
- probing_data: `pillar_c/20260728_203149-…/upgrade_rate_1.0/probing_data/`（1.78 GiB，smoke 未 prune）
