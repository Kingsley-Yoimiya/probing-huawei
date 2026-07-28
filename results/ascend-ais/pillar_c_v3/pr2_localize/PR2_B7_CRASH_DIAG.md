# PR-2 B7 · Crash 诊断

**日期**：2026-07-28  
**parent**：`20260728_185909-pillar-c-v3-pr2-e3-b7`  
**crash 类型**：`python_exception` · FATAL · HCCL notify wait timeout

## 事件轴（本地时区 +08:00）

| 时刻 | 事件 |
|------|------|
| 18:59:54 | torchrun 起 16 worker · `Activating probing in 'followed' mode` |
| 19:00:20 | warmup_done, step 50 |
| 19:00:40 | step_100.marker（`inject_start`） |
| 19:01:00 | SIDECAR_START kind=inline_8a every=1 stall_s=0.25 victim=7 |
| 19:01:00 | SET_BEGIN trigger=`L_ge_100` set_rate=1.0 scope=localize victim=7 |
| 19:01:00 | ATTACH_READY majority (16 ranks) · LOCALIZE_ELAPSED_MS=8836 |
| 19:01:17 | SET_UPGRADE step=139 pid=983508(rank=5) rate=1.0 SET_LATENCY_MS=17527 |
| 19:01:55 | SET_DOWNGRADE step=146 reason=`time` elapsed=29s window_s=15 |
| 19:01:03 | rank_*.jsonl 最后 append（step=145，ts=1785236463.919） |
| 19:01:04+ | 训练 loop 停止 append 新 step；HCCL AllReduce stuck |
| ~19:20:48 | AscendCL 侧 `HeartbeatAbnormal Cluster Exception … Stuck Occurred` at `10.119.7.40/15` |
| 19:31:46 | rank 5 (pid=983508) fatal: `AclrtSynchronizeDeviceWithTimeout error_code=507001` · notify id 3548/3550/3552/3692 全 timeout（1836s/1863s） |
| 19:31:54 | rank 7 (pid=983510) 同样 fatal · Communication_Error_Timeout(EI0002) |
| 19:31:56 | torchrun `ChildFailedError(exitcode=1 local_rank=5 pid=983508)` |
| 19:31:56 | rank 0/2..15 全部 SIGTERM |

## Root cause

- **HCCL notify wait timeout（不是 subproc_pool 死锁）**：
  - `stream_id:47, task_id:60581, notify_id=3548, timeout=1836s`
  - `taskType[Notify Wait], tag[AllReduce_group_name_0ringAllReduceFastDoubleRingFor91093Executor_device]`
  - `AlgType(level 0-1-2):[ring-ring-NHR]`
  - remote rank 报 `[4294967295]`（0xFFFFFFFF）= "local"，说明是本 rank 等自己 device stream 的 notify
- Ascend log 明确指出 `[TaskExecStage][HeartbeatAbnormal]Cluster Exception Location[IP/ID]:[10.119.7.40/15], Stuck Occurred, Possible Reason:1. Host process is stuck, 2. Device task is stuck`
- 命中 IP `10.119.7.40/15` = pod 内 device 15，但 crash 打在 rank 5 的 Device 5（chipId=2 dieId=1）—— 说明 HCCL ring 里 device 15 (rank 15) stuck 导致 rank 5 的 AllReduce 等不到 remote notify
- faulthandler current-thread 栈都在 `probing/crash.py:96 _capture_thread_stacks` → sink 内部；主线程 top_frame 是 `torch_npu/npu/utils.py:72 in synchronize`
- **subproc_pool `_recv_msg` 线程健在但 idle**：不是死锁源；只是 crash handler 把它抓下来了

## Rank-level 归因

| rank | pid | last jsonl step | first fatal | 位置 |
|------|-----|-----------------|-------------|------|
| 5 | 983508 | 145 (19:01:03) | 19:31:46 | notify wait timeout · stream 47/48/49 |
| 7 | 983510 | 145 (19:01:03) | 19:31:54 | 同样 507001 |
| 0/1/2/3/4/6 | 983503..983509 | 145 | 19:31:54 SIGTERM | torchrun 兜底关闭 |
| 8-15 | 983511..983518 | 144-145 | 19:31:54 SIGTERM | 同上 |

- rank 15 (pid=983518) 在 `localize.log` 里就已经报 `ok=False raw_head='timeout'`（19:01:00 SQL 期间就 attach 不上）→ **rank 15 早就有异常**，可能是本次 stuck 的真源头
- rank 5 被 mis-localize 为 culprit，SET rate=1.0 给它 → 训练继续 6 步后所有 rank 卡在 rank 15 的 AllReduce 上

## SET-引发 vs 独立？

**结论：SET_UPGRADE (rate=1.0) 与 crash 不是直接因果，但 mis-localize + rank 15 pre-existing stuck 共同导致训练无法推进**

证据：
1. SET 打给 rank 5，`torch_probe`/`probing.torch.profiling=on,rate=1.0` 在 rank 5 上生效，只影响 rank 5 的 torch tracer 采样率；不改变 collective 语义
2. SET_DOWNGRADE 在 19:01:55 已回 rate=0，`SET_DOWNGRADE_OK pid=983508 reason=time`，可以确认 tracer 已停
3. 训练最后一步 step=145 时 rank 5 `step_ms=466ms compute_ms=447`，看不出 SET 生效期间 rank 5 明显变慢
4. HCCL notify timeout 1836s 从 crash 时刻回推 = 19:01:11 左右开始等 → 恰好在 SET_UPGRADE 之后 & SET_DOWNGRADE 之前的窗口
5. 但 `localize.log` 里 rank 15 `attach=True ok=False raw_head='timeout'` 已在 19:01 就有问题
6. Ascend log 指的 stuck device 是 `10.119.7.40/15` = rank 15，不是 rank 5

**假设**：rank 15 在 sidecar SIDECAR_START (inline_8a for victim=7) 之前就已经异常；SET 给 rank 5 是无害的但也无用的（culprit miss）；真正 stuck 的是 rank 15，其他 rank 在下一次 AllReduce 时全 wait rank 15 → 1836s 后硬崩

## 是否阻塞 B8？

**不阻塞**，但需要在 B8 里加两个防护：

1. **Localize SQL 改 AVG + 拉长窗口** —— 命中 GT rank 7；如果换 SQL 后仍然 mis-localize，需要打印 `AllReduce timeout` 相关的 device 级别 heartbeat 到 `localize.log`
2. **Driver 侧 stop_hang 提早触发** —— 现在 `PILLAR_C_SET_HANG_MAX_S=480s` 是 SET 后 hang 兜底，但依赖 driver 的 poll loop；训练主循环 stall 时 jsonl 停 append，driver 应该在 "no new step for N seconds" 时主动 `kubectl exec kill torchrun` 而不是等 HCCL 1800s 超时
3. **HCCL_EXEC_TIMEOUT 提前**：可以 `export HCCL_EXEC_TIMEOUT=600` 让 HCCL 自己 6 分钟就报错退出（当前默认 1800s = 30min，太长）

如果 B8 换 SQL 后 culprit 命中 rank 7，rank 7 有 sidecar inline_8a stall 0.25s/step + upgrade 生效 → 训练 step 递增可能变慢但不 stuck，rank 15 pre-existing 异常仍需另外查（可能是 hardware quirk 或 device 15 lock）。

## 相关文件

- `probing_data/crash/latest.json` (rank 5 fatal)
- `probing_data/crash/983510/*.json` (rank 7 fatal)
- `P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/node_0.log` (torchrun 主日志)
- `P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/localize.log` (rank 15 attach ok=False raw_head=timeout)
- `P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/set_upgrade.log` (SET+DOWNGRADE 完整时基)
