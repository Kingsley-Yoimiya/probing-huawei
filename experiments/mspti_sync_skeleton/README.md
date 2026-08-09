# MSPTI NPU 同步骨架采集

本目录是隔离实验，不接入 Probing 既有大模块。目标是在 CANN MSPTI 捕获阶段把逐 kernel
trace 压缩为同步分析所需的时序骨架，并保留可直接打开的 Chrome/Perfetto trace。

## 事件语义

- `KSEG`：来自 `MSPTI_ACTIVITY_KIND_KERNEL`。native 后台线程按
  `(device_id, stream_id)` 和 gap 阈值合并连续 kernel；kernel 名不会落盘。
  `count` 表示合并数量，`active_ns` 表示 kernel 活跃时间之和，`span_ns` 表示首尾包络，
  `gap_ns=span_ns-active_ns`。
- `COMM` / `P2P`：优先来自 `MSPTI_ACTIVITY_KIND_COMMUNICATION`，保留 device/stream、
  起止、通信算子、通信域、correlation ID，以及由公开 datatype × count 推导的 bytes。
  CANN 8.5.0 头文件没有说明 HCCL 域 `cbdata` 的参数结构，实测按 runtime 结构读取会崩溃，
  因此不启用 HCCL Callback。
- `HSYNC`：优先订阅 `MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED`；CANN 8.5.0 上
  `correlationData` 不可写（会 SIGSEGV），enter/exit 用 thread_local 配对。
  另在 workload 必要 sync 周围写 host marker。默认不 `LD_PRELOAD` ACL interpose
  （smoke9 失败路径）；仅 `ENABLE_ACL_INTERPOSE=1` 时启用。
  只记录既有 host 阻塞区间，不插入额外同步。
- `STEP`：训练式 workload 通过 collector 自有 C API 写入的 begin/end marker。
- `DROP`：collector 自有内存分配失败、队列溢出和解析失败计数，即使为 0 也落一条记录。

CANN 8.5.0 的 `mspti_cbid.h` 没有 Event/Notify/stream-wait Callback ID，因此本实现没有
`SWAIT`，也不通过猜测 ABI 伪造依赖边。MSPTI 同样没有公开内部 drop counter；
`DROP=0` 只表示 collector 可见路径无丢失。

## 架构

MSPTI `OnBufferCompleted` **在回调内**调用 `msptiActivityGetNextRecord`，深拷贝为
owned `RawEvent` 后入有界队列；原 buffer 按官方「Complete 后所有权归 client，可通过
Request 回收」进入对齐缓冲池（`posix_memalign`），**禁止**把原始指针交后台。
`Unsubscribe` 之后再 `free` 池内基址；重复 Complete 绝不二次释放。Worker 只处理
`kRaw` / host / STEP，JSON 仅在 worker（或 Finalize 收尾）写盘。

### Capture / Finalize 顺序

1. **CaptureBegin**：`msptiActivityEnable(KERNEL/COMM)` + 入队 STEP begin
2. **CaptureEnd**（门控）：入队 STEP end → `capturing=false` →
   `msptiActivityDisable(KERNEL/COMM)`。**无** `FlushAll`、**无** sleep、**无** 假等待
3. **Finalize**（训练结束 / `atexit` / `Stop` 别名；与代码一致）：
   Disable（若仍启用）→ 专用线程 `msptiActivityFlushAll(0)` 并 **join** →
   **真 drain**（队列空 ∧ inflight==0 ∧ worker 空闲；超时 → terminal_failed，
   **有意泄漏** outstanding/订阅，进程级 singleton 不析构）→
   确认 quiescent 后 Disable runtime callback + `msptiUnsubscribe`
   （unsubscribe 失败同样 terminal_failed，不得 close/free）→
   停 worker 并 join → `DrainPending` / flush segments / 写 DROP →
   flush/close 输出 → 仅成功路径 free 缓冲池。

### 封存与本机锚

顺序：`done` / raw exit / meta / jsonl → convert / counters / trace →
`artifact_digest.json`（条目 relative path/size/sha256 + **aggregate_sha256**；
manifest 引用 aggregate，**不是** digest 文件自身 hash）→
`attempt_manifest.json` 原子一次写 → `attempt_manifest.sha256`。
seal 后不得再改被覆盖文件。

父 launcher 回拉后独立写 `LOCAL_VERIFIED_SEAL.json`（含 remote manifest hash、
artifact aggregate、本机关键文件 hash、verify 时间、run_id）。威胁模型是检测
**意外修改**，不是防恶意本机用户；不使用 vault 密钥。formal 接受门禁要求该本机锚。

C API：`mspti_skeleton_finalize()`；统计见 `mspti_skeleton_last_finalize_stats`
（`finalize_flush_ms` / `finalize_drain_ms` / `finalize_complete` /
`raw_kernel_count` / `raw_comm_count`）。`capture_end_ms` 仅为门控耗时。
`convert_trace_write_ms`：convert 内从读 skeleton 到 `cluster.trace.json` 写完的 monotonic wall；不含 `counters.json` 自反写盘。`convert_command_wall_ms` 为 convert 进程外层/命令级 wall（含 counters/SUMMARY）。旧名 `convert_export_wall_ms` 仅为别名。
`e2e_wall_ms`：node runner 外层 monotonic，从训练 argv 启动到进程退出（含 torch profiler export）；多 node 取 max。

编译必须在 CANN pod 内完成；源码按内容哈希落到：

```text
/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei/experiments/mspti_sync_skeleton-<hash>/
```

结果写：

```text
/afs-a3-weight-share/yinjinrun.p-huawei/results/mspti-sync-skeleton/<run_id>/
```

本机备份：`myportal/results/huawei-a3-32/mspti-sync-skeleton/<run_id>/`（含 LOCAL_VERIFIED_SEAL）。

旧 raw-buffer / step Flush 方案已 **superseded**；以本 README 与 collector.cpp 为准。

## 运行

脚本默认先检查借用 pod 没有活 `torchrun` / Megatron / 对方训练。双节点启动并行提交，
运行中持续检查对方训练；发现后只终止本目录 workload，不改、删任何 vcjob。

`launch_megatron_ab.sh` 的 `FIXTURE_RUN=1` 只做 **后处理 parity**（合成 artifact +
同一 postprocess / strict），**不**覆盖真实 SSH/kubectl；编排清理由
`fanout_orchestrator` 本地 fixture 覆盖。

`launch_grj.sh` / `run_node.sh` 路径为 **synthetic**（`arm_kind=synthetic`）：只加载
hash 命名只读 sealed SO，禁止可变 `build/libmspti_sync_skeleton.so`；完成后封存
`run.log` + attempt manifest + LOCAL_VERIFIED_SEAL。

单 rank smoke：

```bash
NNODES=1 NPROC=1 CAPTURE_RANKS=0 STEPS=2 \
  experiments/mspti_sync_skeleton/launch_grj.sh
```

单节点 2 rank：

```bash
NNODES=1 NPROC=2 CAPTURE_RANKS=all STEPS=2 \
  MASTER_PORT=29932 experiments/mspti_sync_skeleton/launch_grj.sh
```

32 卡 rank0-only：

```bash
NNODES=2 NPROC=16 CAPTURE_RANKS=0 STEPS=2 \
  MASTER_PORT=29933 experiments/mspti_sync_skeleton/launch_grj.sh
```

32 卡全 rank 短窗：

```bash
NNODES=2 NPROC=16 CAPTURE_RANKS=all STEPS=2 \
  MASTER_PORT=29934 experiments/mspti_sync_skeleton/launch_grj.sh
```

脚本会生成 `rank_XXXX.skeleton.jsonl`、每 rank trace、`cluster.trace.json`、
`counters.json`、`SUMMARY.md`、配置、环境与完整日志，并立即回拉：

```text
/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton/<run_id>/
```

将 `cluster.trace.json` 拖入 `chrome://tracing` 或
[Perfetto UI](https://ui.perfetto.dev/) 即可查看。
