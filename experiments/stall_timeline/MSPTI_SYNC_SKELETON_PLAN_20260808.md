# MSPTI NPU 同步骨架 Trace：实现与 32 卡验证计划

日期：2026-08-08

## 1. 最终目标

为 Probing 增加一条独立于完整 `torch_npu.profiler` 的轻量采集路径：直接消费
MSPTI Activity / Callback 数据，在捕获阶段按 stream 压缩，只保留训练同步分析需要的
时序骨架。

目标产物不是逐算子性能报告，而是能够回答：

1. 每条 NPU stream 在什么时间段持续执行 kernel，何时出现明显空洞；
2. stream 之间何时发生 Event / Notify wait；
3. host 何时调用 stream、device、event synchronize，并阻塞多久；
4. HCCL collective / P2P 在哪条 stream 上执行，起止时间、通信域和数据量是什么；
5. 上述事件属于哪个 rank、host、device 和训练 step。

## 2. 环境门禁

- 集群：华为 `vc-a3-241ceshi`，借用 `songyiyang.p` 只用于进入集群；
- 结果只写：
  `/afs-a3-weight-share/yinjinrun.p-huawei/results/mspti-sync-skeleton/<run_id>/`；
- 主池 `yysong` 优先；当前主池无运行 pod 时，才允许使用空闲的
  `grj-megatron-32card-0716` 两个 16 卡 pod；
- 使用 `grj` 前必须确认无活 `torchrun` / Megatron / 对方训练；对方进程出现时立即停止；
- 不删除或修改任何借用 vcjob；
- 编译仅在 Ascend pod 内进行，target 与源码放自有 AFS，不在跳板 `/tmp` 全量编译；
- 每轮日志和结果目录必须带时间戳，关键里程碑立即回拉本机。

已确认：

- 本机通道和 `ais-cf3e61a5` 跳板可用；
- kube context 为 `songyiyang.p`；
- 备选的两个 `grj` pod 当前 Running，未发现活训练进程；
- pod 为 CANN 8.5.0；
- `libmspti.so` 与 `include/mspti/mspti.h` 存在；
- Python `mspti` 模块不存在，因此正式实现走 native C/C++ 或 Rust FFI。

## 3. 捕获架构

### 3.1 MSPTI Activity

只启用：

- `MSPTI_ACTIVITY_KIND_KERNEL`
- `MSPTI_ACTIVITY_KIND_COMMUNICATION`
- 必要时启用 `MARKER`，用于 step/rank 关联

不启用完整 API、内存、栈、shape、AI Core metrics。

> **2026-08-09 supersede（第四轮）**：旧方案“Complete callback 把 raw buffer 交给后台线程”
> 已废弃。现行契约：
>
> 1. **Request** `posix_memalign` 基址进入 `outstanding`；
> 2. **Complete** 回调内 `GetNextRecord` → 深拷贝为 owned event 入队 → **exactly-once**
>    归还/释放原 buffer（scope-guard + `catch(...)`，不跨 C ABI）；
> 3. worker 只消费 owned event，**禁止** raw MSPTI 指针入队；
> 4. Drain 超时若仍有 outstanding：标 `terminal_failed` 并有意泄漏，**禁止**
>    `FreeAllBuffers` / close / Unsubscribe 仍可能被写的状态。

### 3.2 Capture / Finalize（两阶段）

> **supersede**：旧文要求 CaptureEnd 调用 `msptiActivityFlushAll` 已废弃。

1. **CaptureBegin**：Enable KERNEL/COMM + STEP begin；
2. **CaptureEnd**：**仅** Disable Activity + STEP end；**禁止** FlushAll / 固定 sleep / 写最终 DROP；
3. **Finalize**（显式训练结束路径：`last_train_step` / `train.finally` / `pretrain.finally`）：
   Disable → `FlushAll(0)`（专用线程 join）→ 真 drain → Unsubscribe → join worker →
   写 DROP（失败则 Finalize 失败）→ close。`atexit` 仅兜底，strict 拒绝。

### 3.3 MSPTI Callback / 必要的薄拦截

选择性订阅：

- runtime stream synchronize；
- HCCL AllReduce、Broadcast、AllGather、ReduceScatter、Barrier、Send、Recv；
- 若 MSPTI callback 未暴露 Event / Notify wait 参数，则仅对
  `aclrtRecordEvent`、`aclrtStreamWaitEvent`、Record/Wait Notify 做薄拦截。

不拦截所有 host API，不采集 host 栈。

### 3.4 捕获时压缩

每个 `(device_id, stream_id)` 维护一个开放的 kernel segment：

- 相邻 kernel 间 gap 小于阈值且中间没有通信、wait、sync、step 边界时合并；
- 遇到长 gap 或语义边界时关闭 segment；
- 丢弃 kernel 名，只保留 kernel 数、活跃时间、包络时间和空洞时间；
- communication 始终独立保留，不并入普通 kernel segment；
- Activity 乱序时使用有限 watermark 重排，不做无限缓存。

## 4. 最小事件模型

统一固定字段：

```text
run_id, rank, host, device_id, stream_id, step,
kind, start_ns, end_ns, seq, peer_stream, correlation_id,
count, bytes, flags
```

事件种类：

- `KSEG`：连续匿名 kernel 段；
- `SWAIT`：stream 等待 Event / Notify 的依赖边；
- `HSYNC`：host synchronize 调用及 host 阻塞区间；
- `COMM`：HCCL collective；
- `P2P`：HCCL send / recv；
- `STEP`：训练 step marker；
- `DROP`：buffer 或记录丢失计数，禁止静默丢数据。

热路径使用定长二进制记录或 Probing memtable；仅在导出阶段生成 JSONL /
Chrome Trace JSON。

## 5. 实施阶段

### P0：MSPTI 原生 smoke

1. 在单 rank pod 中编译最小 native collector；
2. 捕获 `KERNEL` 与 `COMMUNICATION` Activity；
3. 运行短小的 matmul + AllReduce 合成环；
4. 验证 start/end、device/stream、communication 字段非零且 callback 能正常 flush；
5. 记录 CANN/MSPTI ABI、编译命令和完整日志。

### P1：在线压缩

1. 实现 per-stream KSEG 聚合；
2. 增加 gap threshold、step flush 和 drop counter；
3. 导出骨架 JSONL 与 Chrome Trace JSON；
4. 对同一短窗同时抓一次完整 profiler，核对 KSEG 包络与 COMM 起止；
5. 比较原始记录数、压缩记录数和文件体积。

### P2：host 同步与 wait 边

1. 接入 stream/device/event synchronize 的 host enter/exit；
2. 记录 Event/Notify producer → consumer stream 关系；
3. 加入 HCCL callback 的 op/comm/bytes/correlation；
4. 明确 host 与 device 时钟域；无法可靠对齐的字段不得混算 duration。

### P3：32 卡训练状态验证

1. 两个 16 卡 pod，32 ranks；
2. 先做短训练/训练式循环，预计 1～3 分钟；
3. 先只在 rank 0 开启 KERNEL+COMM，其他 rank 仅 host/COMM；
4. 验证稳定后再做 32 rank 短窗，严格限制为 1～2 step；
5. 输出每 rank trace、聚合 trace、host/device/stream 字典、运行配置和日志；
6. 结束后停止我们启动的进程，不修改或删除 hold vcjob。

## 6. 性能验证

同一 workload 做 counterbalanced AB：

- A：无 collector；
- B：sync/COMM only；
- C：KERNEL+COMM 在线压缩；
- D：完整 `torch_npu.profiler` 短窗。

报告：

- step p50 / p95 / p99；
- 吞吐下降比例；
- callback CPU 时间；
- MSPTI 原始记录数、KSEG 数和压缩比；
- bytes/step；
- DROP 数；
- flush 尖刺。

目标而非预设结论：

- sync/COMM only：p50 开销不超过 1%；
- KERNEL+COMM：显著低于完整 profiler，且短窗内无 DROP；
- 压缩后记录数至少下降一个数量级；
- 任何开销目标未达成都保留为实验结果，不隐藏。

## 7. 32 卡交付物

远端：

```text
/afs-a3-weight-share/yinjinrun.p-huawei/results/mspti-sync-skeleton/<run_id>/
  run.log
  config.json
  environment.txt
  rank_XXXX.skeleton.jsonl
  rank_XXXX.trace.json
  cluster.trace.json
  counters.json
  SUMMARY.md
```

本机立即备份：

```text
myportal/results/huawei-a3-32/mspti-sync-skeleton/<run_id>/
```

用户验收入口优先提供：

- `cluster.trace.json`：可直接加载 Chrome / Perfetto trace viewer；
- `SUMMARY.md`：解释每条 lane、字段、底层 MSPTI/HCCL API 与测量条件；
- 一张静态 SVG 总览，仅在 trace 本身不便快速预览时生成。

## 8. 停止条件

- MSPTI ABI/头文件与运行库不一致；
- collector 导致训练错误、hang、明显 desync；
- DROP 持续增长且无法通过 buffer 调整解决；
- KERNEL 模式开销明显接近完整 profiler；
- 对方训练进程出现；
- AFS 权限或落盘前缀不正确；
- 两个 pod 中任一节点异常。

## 9. 真实 Megatron 20-step 三臂对照（2026-08-08 追加）

> 纠正：此前 `mspti_sync_skeleton/workload.py` 仅为 synthetic compute+AllReduce，
> **不是** Megatron。本节才是真实训练对照。

### 9.1 已验证启动命令来源

复用 `plans/case-b-512card/sync_stall/fire_dense_l0_smoke_grj32.sh` /
`fire_jitter_mvp_grj32.sh` 的 dense 配置（grj32 上已多次跑通）：

- 代码：`/MindSpeed-LLM/MindSpeed-LLM/pretrain_gpt.py`
- 并行：TP=2 PP=1，2 节点 ×16 卡，GBS=64，SEQ=4096，layers=32，hidden=4096
- 数据：`/afs-a3-weight-share/enwiki/enwiki20230101/enwiki20230101-00000_text_document`
- cache：`/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache`
- 结果只写：`.../results/mspti-sync-skeleton/megatron-ab/<group_id>/`

### 9.2 三臂设计

同一批 pod、同一模型/数据/seed=1234/并行，严格顺序，不同 `MASTER_PORT`：

| arm | 含义 | 采集 |
|---|---|---|
| A `normal` | 无我们的 collector、无 torch profiler | 无 |
| B `ours` | MSPTI skeleton **仅 1 个稳定 step** | `mspti_skeleton_start_gated` + `capture_begin/end` |
| C `torch` | `torch_npu.profiler` **仅 1 个相同位置 step** | MindSpeed `--profile --profile-level level0 --profile-with-cpu --profile-data-simplification`，shapes/memory/stack 全关 |

- 目标 step：Megatron 日志 `iteration 10`（1-based，train_step 完成后打印）。
- ours 映射：`curr_iteration + 1 == 10` 时门控开关 Activity。
- torch 映射：`--profile-step-start 10 --profile-step-end 11`（MindSpeed schedule active=1）。
- 预计每臂：启动 2–4 min + 20×~2.5 s ≈ 5–10 min；torch flush 另计。

### 9.3 计时 / 大小口径

- **20-step wall**：node0 日志中全部已记录 `elapsed time per iteration (ms)` 之和（排除环境准备/编译；启动时间单列）。
- **steady p50/p95**：iteration 5–20 且 **排除 capture step**。
- **trace step**：iteration=10 的 elapsed，及其相对邻近正常 step 中位数 / normal 同位置的增量。
- **init/flush 尖刺**：ours 的 collector_start / capture_begin / capture_end（**无** ActivityFlush；Flush 只在 Finalize） / stop；torch 的 profiler start/stop/export；不得混入 steady。
- **吞吐**：保留 Megatron 日志 samples/s、tokens/s/GPU、TFLOP/s/GPU（若有）。
- **大小**：ours=`cluster.trace.json` + 全 rank skeleton JSONL 总和 + 目录总量 + rank0；
  torch=最终 `trace_view.json`（Ascend profiler 主产物，位于 `torch_prof_node*/**/ASCEND_PROFILER_OUTPUT/`）总和 + profiler 目录总量 + rank0；旧 `trace_view.json` 名称已废弃。

### 9.4 Gate 机制（ours）— 2026-08-09 supersede

1. **仅 training worker**（`pretrain_gpt` + `LOCAL_RANK`）在 sitecustomize 内、NPU init 前
   `mspti_skeleton_start_gated`：注册 MSPTI callback，**不** enable KERNEL/COMM。
2. 目标 step `capture_begin`：enable KERNEL/COMM Activity + STEP begin。
3. 目标 step 结束后 `capture_end`：STEP end + **仅 Disable** Activity（**无** FlushAll）。
4. 训练结束显式 `Finalize`：`FlushAll(0)` + drain + Unsubscribe + DROP；绑定真实
   MindSpeed `train_step`/`train`/`pretrain`（禁止 Megatron→MindSpeed 反向覆盖）。
5. 不插入训练语义同步；Finalize 尾税单列，不得把 `capture_iter+1` 标成 Flush。

> 旧文“capture_end 含 ActivityFlush”已 superseded，禁止回退。

### 9.5 正式结果指针（group）

- group：`20260808_232810-megatron-ab20`
- arms：`...-normal` / `...-ours`（early-gate 重跑） / `...-torch`
- 本机：`myportal/results/huawei-a3-32/mspti-sync-skeleton/megatron-ab/20260808_232810-megatron-ab20/`
- smoke（2-step，证 KSEG/COMM）：`20260809_001500-megatron-ours-smoke2`

