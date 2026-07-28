# PR-2 实验 B7 · PARTIAL

**日期**：2026-07-28  
**parent**：`20260728_185909-pillar-c-v3-pr2-e3-b7`

## 头条 · 五指标

| 项 | 值 |
|----|-----|
| 头条比 | **47.67%**（`raw_dump`；v2 参考 72.6%；目标 <100%） |
| dense_ranks | **0**（B7 无 `python.torch_trace` dump） |
| culprit_rank | **5**（GT=7；⚠ mis-localize，见诊断） |
| culprit_pid | 983508 |
| LOCALIZE_FALLBACK | **0**（SQL 命中，非 fallback） |
| SET_OK / SET_DOWNGRADE_OK | **Y / Y**（reason=`time` elapsed=29s upgrade_step=139 downgrade_step=146） |
| SET_LATENCY_MS | 17527 |

- W\* content est: **None** —— 本轮 B7 无 `python.torch_trace` 文件（`comm_collective` 也未 dump），估算尺分母缺失
- same_cover（RSS 够归因）: **Y** —— RSS 200 采样，max=2.32GB，rise=6.2MB
- 分臂字节：动态 **854 MB**（其中 `python.torch_step_timing` 335MB · cpu/gpu.utilization 各 134MB · cpu.tasks 134MB · gpu.hccs 67MB · cold `.memc` 48.7MB · 三个 extra_pid 残留 62KB），全量 REUSE=1.79 GB

## Extra pid prune（P2 gate 验证 PASS）

`_work/prune_extra_pids.log`：

```
PRUNE_EXTRA_PIDS root=…/probing_data worker_pids=[] culprit_keep=[] kept=16 removed=18 ignored=1 dry_run=False
removed=982969,983441,984431,984489,984568,984628,984669,984693,984725,984767,985047,985057,985100,985139,985179,985209,985260,985278
kept=983503..983518
```

- `kept=16` = 全部 worker_pid（983503..983518）
- `removed=18` = extra torchrun/launcher/utility pids
- `ignored=1` = crash 目录（不在 pid 名单）
- 语义验证 **PASS**：P2 gate 按 `worker_pids.txt` 精确保留 16 个 rank + `crash/` metadata

## Orchestration 完整摘要

- FIRE_OK → warmup_done → step_100.marker → LOCALIZE_SQL(step=139, window=[119,139], culprit_rank=5, culprit_pid=983508, elapsed=8836ms, fallback=0)
- ATTACH_OK pid=983508（0 retries）→ SET_TARGET=`probing.torch.profiling=on,rate=1.0` @19:01:17 → SET_LATENCY=17.5s
- SET_DOWNGRADE @19:01:55 reason=time window_s=15 elapsed_s=29 upgrade_step=139 downgrade_step=146
- SIDECAR_START kind=`inline_8a` every=1 stall_s=0.25 victim=7
- `injection.log`: `SIDECAR_START kind=inline_8a every=1 stall_s=0.25 victim=7`
- 到 step 146 全 rank 停写 `rank_*.jsonl`，`inject_stop marker (step_300)` **未生成**
- volume_at_upgrade: `hot_memt=0 hot_bytes=0 cold_segs=68 cold_bytes=44512784 rows_overwritten_sum=0`

## B7 crash 摘要（详见 `PR2_B7_CRASH_DIAG.md`）

**根因**：训练在 `step 146` 后 hang，culprit rank 5(pid=983508)在 HCCL AllReduce 内层 `notify wait`，`HCCL_EXEC_TIMEOUT` 默认 1800s 到点后 `AclrtSynchronizeDeviceWithTimeout error_code=507001` 硬崩，19:31:46 rank 5 first fatal → 19:31:54 rank 7 second fatal → torchrun 收 ChildFailedError(exitcode=1 local_rank=5)。

- crash 时刻：19:31:46（`crash/983508/latest.json` timestamp_ns=1785238306.7 → 19:31:46+08:00）
- crash 记录里 `global_step=195` 是 probing crash sink 的 fingerprint 计数器，非训练循环 step；实际 rank_*.jsonl 最后一行是 `step=145 ts=19:01:03`
- SET_UPGRADE (step=139) → 训练继续到 step=146 → 之后 stall → 30 min 后 HCCL notify wait 超时
- `[TaskExecStage][HeartbeatAbnormal]Cluster Exception … Stuck Occurred, Possible Reason: Host process is stuck 或 Device task is stuck` at `10.119.7.40/15`
- 独立 pytorch subproc_pool（inductor）线程健在，只是 dispatch idle；faulthandler 主线程栈都在 `crash.py` sink 里 → **crash 由 HCCL notify wait timeout 触发，并非 subproc_pool 死锁**

## 判定：**PARTIAL**

**PARTIAL 原因**：

1. **`culprit_rank=5 ≠ GT=7`**（P3-SW-A `inline_8a` GC 让全 rank 同步 wait，step_ms max over `[119,139]` 抓到 rank 5 metric=0.396，rank 7=0.266 反而低；不是 code bug，是 localize SQL 判据不 robust）
2. **训练在 step 146 hang → 30min 后 HCCL 硬崩**，没跑到 inject_stop=300，未到 ITERS=1000；`step_300.marker` 缺失
3. **dense_ranks=0**：因 SET 打给 rank 5 后训练很快 stall，torch_trace 没来得及 dump；`python.comm_collective` 也不在 probing_data（B7 gate `PROBING_TORCH_COMM_COLLECTIVE_LAZY=1` 开启，未采样也未落盘）
4. **头条 47.67%**（<72.6% v2）但因 `dense_ranks=0` + 训练未跑完，信度受影响；不能作为"lazy+prune 有效降体积"的最终结论

**同时验证 PASS 的语义**：

- P2 `prune_extra_pids`：kept=16 removed=18 ignored=1 全 gate 生效
- P1 `PROBING_TORCH_COMM_COLLECTIVE_LAZY=1`：`comm_collective` 完全未落盘（对比 B5d 有 713MB）→ lazy gate 有效
- SET_UPGRADE + SET_DOWNGRADE 时基降回：15s 窗，elapsed=29s，`reason=time` 原生降回
- LOCALIZE_FALLBACK=0：SQL 命中，非兜底

## 与 B5d 对比

| 项 | B5d | B7 |
|----|-----|-----|
| 头条 | 115.05% | **47.67%**（raw） |
| dense | 1 | 0 |
| culprit | 7 | **5**（mis） |
| SET_OK/DG | Y/Y(time,60s) | Y/Y(time,29s) |
| 训练完成 | L=1000 | **crash @ step 146** |
| `comm_collective` bytes | 713 MB | **0**（lazy gate） |
| `torch_trace` bytes | 335 MB | **0** |
| dump total | 2273 MiB | **815 MiB**（-64%） |
| extra_pid prune | 未启用 | **kept=16 removed=18** |

- lazy comm/step + prune 确实把体积压下来了（-64%）
- 但 dense_ranks=0 + culprit miss 使得 v2 参照对比不成立；本轮 headline 不能作为最终 PR-2 数字

## 下一轮建议（供主 Loop 派 B8）

1. **localize SQL 判据改 AVG**：`ORDER BY AVG(step_duration_sec) DESC` 而非 `MAX(…)`；P3-SW-A `inline_8a` GC 每步 stall 0.25s，AVG 才能反映 rank 7 持续被 sidecar 挂住
2. **拉长窗口到 50-100 步**：`PILLAR_C_LOCALIZE_WINDOW=50` 或 `100`，稀释瞬时 GC 抽奖
3. **降 HCCL_EXEC_TIMEOUT 或加 stop_hang 兜底**：现在 stop_hang=480s，比 HCCL 默认 1800s 短，正常应该先触发 driver stop_hang → 但训练 loop 已 stall 无 poll → 需要在 driver 侧加"detect no jsonl append > 90s 主动 kill torchrun"
4. **补 crash 是否阻塞 B8**：从证据看是 HCCL notify wait timeout（rank 间 stuck）不是 subproc_pool 死锁；如果换 AVG SQL 后 culprit 命中 rank 7，rank 7 sidecar 主动 stall + upgrade 生效，其他 rank AllReduce 等它的窗口更长；HCCL_EXEC_TIMEOUT 应从默认 1800s 拉高到 3600s 或 5400s 兜底

## 产物路径

- STATUS: `pr2_localize/PR2_EXP_B7_STATUS.md`
- 判分: `pr2_localize/PR2_E3_RATIO_B7.{md,json}`
- crash 诊断: `pr2_localize/PR2_B7_CRASH_DIAG.md`
- 本地 dump: `pr2_localize/20260728_185909-pillar-c-v3-pr2-e3-b7/dynamic/` (rsync from `pillar_c/…/upgrade_rate_1.0/`)
- Pod AFS: `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_185909-pillar-c-v3-pr2-e3-b7/`
