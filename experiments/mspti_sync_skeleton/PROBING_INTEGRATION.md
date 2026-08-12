# MSPTI 同步骨架 → Probing 正式接入

本文说明实验目录 `experiments/mspti_sync_skeleton/` 与 Probing 主路径
`python/probing/profiling/npu_sync/` 的关系、启用方式与不可越界的结论边界。

## 1. 两条路径各自负责什么

| | 实验目录 | Probing 主路径 |
|---|---|---|
| 位置 | `experiments/mspti_sync_skeleton/` | `python/probing/profiling/npu_sync/` |
| 入口 | `launch_grj.sh` / `launch_megatron_ab.sh` + 自带 `megatron_mspti_hook.py` | `probing.ext.torch.init()` → `maybe_start()` |
| 产物 | JSONL + Chrome/Perfetto trace + `counters.json` + seal / manifest | JSONL + `python.npu_sync_*` SQL 表 |
| 用途 | A/B 编排、封存、strict 门禁、开销对比 | 训练进程内随 Probing 一起采集、SQL / skill 查询 |

**实验目录保持可独立运行，不被删除也不被主路径调用。** 它仍是 collector.cpp、
封存协议与 A/B 编排的真相源；主路径只复用它的 **C API 与落盘契约**。

`collector.cpp` 只有一份（在实验目录，必须在 CANN pod 内编译）。主路径通过
`ctypes` 加载编译好的 `.so`，不重新实现 native 侧。

## 2. 模块归属

按 `docs/src/design/modularity.zh.md`：

- **L2 采集**：`python/probing/profiling/npu_sync/` — 只写自己的表，不调用其他 collector。
- **L4 体验**：`skills/npu_sync_skeleton/` — 只用 SQL 读表，不含业务代码。
- 接线点是既有的 `probing/ext/torch.py::init()`，没有新增组装根。

| 文件 | 职责 |
|---|---|
| `config.py` | 开关与参数解析（纯逻辑，不 import probing） |
| `skeleton.py` | JSONL 解析、DROP / KSEG 不变量、按 rank 汇总（纯 stdlib） |
| `collector.py` | ctypes 绑定 start_gated / capture / finalize，可注入 loader |
| `records.py` | `@table` → `python.npu_sync_skeleton`、`python.npu_sync_capture` |
| `ingest.py` | 封存后的 JSONL → 表 |
| `hook.py` | Megatron `train_step` 门控 + finalize + ingest |

## 3. 启用方式（默认关闭）

必须**显式**打开，且只在 training worker 进程（有 `RANK` / `LOCAL_RANK`）生效：

```bash
export PROBING=1
export PROBING_NPU_SYNC_SKELETON=1
export PROBING_NPU_SYNC_SKELETON_LIB=/afs-a3-weight-share/yinjinrun.p-huawei/\
probing-huawei/experiments/mspti_sync_skeleton-<hash>/build/libmspti_sync_skeleton.so
export PROBING_NPU_SYNC_SKELETON_OUT_DIR=/afs-a3-weight-share/yinjinrun.p-huawei/results/<run_id>
export PROBING_NPU_SYNC_SKELETON_RANKS=0        # 默认 0；也可 all 或 0,3,7
export PROBING_NPU_SYNC_SKELETON_STEP=10        # 门控哪一个训练迭代
```

等价的 config key（优先级更高）：`probing.npu.sync_skeleton.{enable,library,out_dir,ranks,step,gap_us,reorder_us,ingest}`。

`PROBING_NPU_SYNC_SKELETON_INGEST=0` 可只落 JSONL、不写表（想完全走实验目录后处理时用）。

### 与实验目录的 `MSPTI_*` 变量

主路径**接受** `MSPTI_COLLECTOR_LIB` / `MSPTI_OUT_DIR` / `MSPTI_CAPTURE_RANKS` /
`MSPTI_CAPTURE_MEGATRON_ITER` / `MSPTI_GAP_US` / `MSPTI_REORDER_US` 作为参数回退，
方便从实验脚本迁移。

但 **`MSPTI_SKELETON=1` 不会启用主路径采集**。实验目录自带 hook，两个 hook 同时启动
会在一个进程里重复订阅 MSPTI。跑实验脚本时保持 `PROBING_NPU_SYNC_SKELETON` 未设即可。

**两个显式开关同时打开是硬冲突**：`PROBING_NPU_SYNC_SKELETON=1` 与 `MSPTI_SKELETON=1`
同时为真时，主路径拒绝启动并打 ERROR（`config.conflict_reason()`），不会退化成
「悄悄双订阅」。迁移环境容易残留 `MSPTI_SKELETON=1`，所以这条是 fail-closed 而不是警告。
二选一：用主路径就 `unset MSPTI_SKELETON`，用实验 hook 就 `unset PROBING_NPU_SYNC_SKELETON`。

## 4. 数据契约

### `python.npu_sync_skeleton`（逐事件）

`rank, host, seq, kind, step, device_id, stream_id, peer_stream, correlation_id,
count, bytes, start_ns, end_ns, active_ns, span_ns, gap_ns, op, comm_name, source, flags`

`kind` 语义与实验 README 一致：

- `KSEG`：合并后的 kernel 段。`active_ns` 是段内 kernel 区间**并集**，`span_ns` 是首尾包络，
  `gap_ns = span_ns - active_ns`，`count` 是被合并的原始 kernel 数；kernel 名不落盘。
- `COMM` / `P2P`：MSPTI COMMUNICATION Activity，`bytes` 由公开 datatype × count 推导。
- `HSYNC`：runtime `STREAM_SYNCHRONIZED` 回调围出的**主机阻塞窗口**，不是设备侧同步原语。
- `STEP`：采集门控 begin/end marker。
- `DROP`：collector 自身可见的丢弃记账，每 rank 恰好一条且必须是最后一条。

**没有 `SWAIT`。** CANN 8.5.0 的 `mspti_cbid.h` 无 Event / Notify / stream-wait 回调 ID，
不采集也不猜 ABI 伪造依赖边。MSPTI 也不公开内部 drop counter，`drop_total = 0`
只代表 collector 可见路径无丢失。

### `python.npu_sync_capture`（每 rank 一行）

采集可信度门禁。**`trustworthy = 0` 的 rank 不得用于下结论**，skill 会直接把它排除。

`trustworthy = 1` 需要同时满足三组条件：

| 维度 | 判据 | 列 |
|---|---|---|
| 事件流完整 | `STEP` begin/end 恰好各一次、且落在请求的 step；全文件同一 rank 且与请求 rank 一致；`DROP` 是最后一条且 seq 最大；至少一条 `KSEG` | `integrity_ok` |
| finalize 干净 | sidecar 存在且 `finalize_rc = 0`、`capture_end_rc = 0`、`finalize_complete = true`、`incomplete = false`、`armed_fail = false`、`raw_kernels` 与 `sum(KSEG.count)` 一致，reason 是显式的 | `meta_ok` |
| 无可见丢弃 | `drop_total = 0`，`DROP` 带 `finalize=1;incomplete=0`，KSEG 不变量成立 | `drop_total` / `finalize_marked` |

不满足时逐条原因写在 `untrustworthy_reason` 列。

**为什么必须读 sidecar**：`DROP` 行是 native collector 在最终 flush / close **之前**写的。
一个 rank 完全可能带着 `finalize=1;incomplete=0` 落盘，随后在关闭阶段失败。只看 JSONL
会把这种产物判成可信。sidecar 是 `rank_XXXX.npu_sync_meta.json`（主路径）或
`rank_XXXX.mspti_meta.json`（实验目录），缺失即判不可信。

`atexit` 兜底 finalize 同样判为不可信 —— 只有 `last_train_step` / `train.finally` /
`pretrain.finally` 三个显式收尾路径算干净采集。

**语法合法 ≠ 可信**：只含一条合法 `DROP`、没有 `STEP` / `KSEG` 的 JSONL 能通过 JSON
解析，但 `integrity_ok = 0`，判不可信。收尾失败的产物仍然会 ingest 事件（那是排障
唯一证据），但 capture 行必须是 `trustworthy = 0`。

### Chrome / Perfetto

主路径不生成 trace。需要时间轴时仍用实验目录：

```bash
python3 experiments/mspti_sync_skeleton/convert_trace.py <run_dir>
```

生成的 `cluster.trace.json` 可直接拖进 `chrome://tracing` 或 Perfetto UI。

## 5. 顺序约束（照抄实验骨架，勿改）

1. `start_gated` 必须在 training worker 内、CANN/NPU 初始化之前 —— 所以挂在
   `torch` import hook 上，不能挪到更晚的训练回调。
2. `capture_end` **只门控**（Disable KERNEL/COMM），无 FlushAll、无 sleep。
3. `finalize` 才做 `msptiActivityFlushAll(0)` + 真 drain + Unsubscribe + 写 DROP + 关文件；
   幂等，失败不得当成功。
4. ingest 只在 finalize 之后跑：`DROP` 是流结束证明，缺它 `skeleton.py` 直接拒收。
   `hook.finalize()` 把 collector 的真实 finalize 结果（rc / complete / reason）作为
   `meta` 传给 ingest，capture 行的 `trustworthy` 由它和事件流完整性共同决定。

### Megatron patch 时序

`train_step` 门控与 `train` / `pretrain` 收尾是就地 patch。真实路径上
`mindspeed_llm.training.training` 的模块体会**先 import torch**（从而武装 collector）
**再定义** `train_step`：此刻模块已在 `sys.modules` 且 `__spec__._initializing` 为真。

- `_patch()` 在 `_initializing` 时直接跳过 —— 属性还不存在，patch 必然失败。
- `_defer_patches()` 对已在 `sys.modules` 的目标**不注册** import-hook 一次性回调，
  否则回调会在模块体执行到一半时被消费掉，之后永不再触发。
- 改用两层重试：包一层 `builtins.__import__`（此后任何一次 import 都是重试机会，
  成功即自摘），加一个有界后台轮询线程（200 × 50ms）兜住「后面再没有 import」的情况。
- patch 目标同时覆盖 package export 与脚本入口（`mindspeed_llm.training`、
  `megatron.training`、`pretrain_gpt`、`__main__`），避免真实入口提前抛异常时
  只能退回不可信的 `atexit`。

## 6. 结论边界（重要）

正式 6×20 两组（`20260809_163543`、`20260809_191657`）均为 **GROUP_INVALID**，
只能作为「已落盘观测」引用，**不可**写成 n=2 或 GROUP_COMPLETE 的性能结论。
`20260809_191657` 的 INVALID 原因是 a06 worker **SIGSEGV**。

本次集成不以新一轮正式 A/B 为前置，也没有把上述观测升格为结论。

另外，2026-08-08 之前的旧产物（如 `megatron-ab/20260808_232001-*`）用的是旧写路径与旧
DROP flags，会被现在的严格解析拒收；这是预期行为，不要为兼容旧格式放宽门禁。

## 7. 测试

```bash
python -m pytest tests/unit/probing/profiling/test_npu_sync.py -q
```

纯逻辑（门控 / 解析 / collector 记账）不依赖 CANN，也不依赖 Rust `_core`：
测试直接按文件路径加载这几个模块，因此在开发机上即可运行。
`records.py` / `ingest.py` 依赖真实引擎，需要 `make develop` 后的环境。
