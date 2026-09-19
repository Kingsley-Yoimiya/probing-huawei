# HCCL issued-work ledger

`hccl_issued_ledger.cpp` 是独立于 MSPTI collector 的 host-side 真值源。它通过
`LD_PRELOAD` 截获 `libtorch_npu.so -> libhccl.so` 的六个公开入口：

- `HcclAllReduce`
- `HcclAllGather`
- `HcclReduceScatter`
- `HcclBroadcast`
- `HcclSend`
- `HcclRecv`

记录语义分两层：进入 HCCL API 即为 `issued`；真实函数返回 `rc=0` 才为
`accepted`。这能独立回答训练进程向 HCCL 发出了多少工作，但不能把一个 HCCL 调用
未经校准地等同于一个 MSPTI `COMM` record。

## 热路径与失败语义

- 每进程固定 8192 条 ring，不动态扩容。
- 已门控的 HCCL 调用只写预分配内存和 atomic；不写文件、不 flush、不做设备同步。
- `overflow_count>0`、未返回调用、HCCL 非零返回、符号未绑定、begin/end 不唯一或
  step 不匹配都会令 summary `pass=false`，`finalize()` 返回非零。
- `finalize()` 才原子写：
  `rank_XXXX.hccl_issued.jsonl` 与
  `rank_XXXX.hccl_issued_summary.json`。
- summary 保留 ledger 自身 DSO 路径、每个真实 HCCL 符号的 `dladdr` 路径和
  self-interpose 检测结果，可纳入 provenance/seal。

## 构建

在 CANN 训练镜像内：

```bash
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -shared -fPIC -pthread \
  hccl_issued_ledger.cpp -ldl -o libhccl_issued_ledger.so
```

启动训练前设置：

```bash
export HCCL_ISSUED_LEDGER_OUT_DIR=/afs-a3-weight-share/yinjinrun.p-huawei/results/<run_id>
export LD_PRELOAD=/sealed/path/libhccl_issued_ledger.so${LD_PRELOAD:+:$LD_PRELOAD}
```

若 `RTLD_NEXT` 不能稳定找到真实库，可显式设置：

```bash
export HCCL_ISSUED_LEDGER_HCCL_SO=/usr/local/Ascend/cann-8.5.0/lib64/libhccl.so
```

必须对 ledger SO 做 content hash 封存，并在目标 pod 用 `nm -D libtorch_npu.so`
确认六个 `U Hccl*` 入口仍存在。若发现 `HcclAllGatherV`、
`HcclReduceScatterV`、`HcclBatchSendRecv` 等新热路径，本版本应 fail closed，不得把六符号
覆盖误称为完整 HCCL schedule。

## 门控 API

训练 step wrapper 通过 `ctypes.CDLL(None)` 获取三个 C API：

```c
int hccl_issued_ledger_begin(int64_t step);
int hccl_issued_ledger_end(int64_t step);
int hccl_issued_ledger_finalize(void);
```

建议边界：先 `ledger_begin(step)`，再启动 MSPTI capture；训练 step 返回后先结束 MSPTI
capture，再 `ledger_end(step)`。训练完整结束时显式 `ledger_finalize()`。ledger 与 MSPTI
共用同一个逻辑 step 边界，但各自落盘、计数和 provenance 独立。

## 证据边界

严格链条应是：

```text
HCCL accepted ledger
  -> MSPTI callback parsed
  -> collector enqueued/worker/processed/emitted
  -> skeleton JSONL
```

若 ledger 比 MSPTI 多，而 MSPTI 之后的 buffer-audit 守恒，则可把缺失严格限定在
“HCCL API 返回成功之后、collector `NoteParsed` 之前”。具体属于 HCCL 内部、MSPTI
producer、callback payload 还是 `msptiActivityGetNextRecord`，现有公开接口仍不能继续拆分。

Python `torch.distributed` monkey patch 可以补充 DP/TP group 和 callsite 标签，但会漏掉
提前 import 的别名、private base API、ProcessGroup/C++ 直调，不能替代 HCCL ledger。
跨 rank device timestamp、peer median、20-bin coverage 也仍是定位 heuristic，不是
expected-work 真值。

## 本地测试

测试用 stub `libhccl` 验证六符号转发、entry/return、门控、binding provenance、原子输出、
幂等 finalize，并用第 8193 条记录验证 overflow fail-closed：

```bash
python3 test_hccl_issued_ledger.py
```
