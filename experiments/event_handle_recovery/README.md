# D51 ACL Event LD_PRELOAD

独立实验目录，拦截 ACL Event API（非 Notify、非 Synchronize），离线重建 event 代次并与 rank0 CANN_API / COMMUNICATION_OP 对齐。

## 文件

| 文件 | 作用 |
|------|------|
| `event_trace_format.h` | 定长二进制 ABI |
| `event_interpose.cpp` | LD_PRELOAD 库 |
| `train_event_preload.py` | 16 卡 DDP 短训 |
| `smoke_event.py` | 单卡两流 smoke |
| `analyze_event_pairs.py` | DB 对齐与链验收 |
| `run_d51_event_preload.sh` | 远端一键流程 |

## 本地单测（Mac）

```bash
./build.sh local
export LD_LIBRARY_PATH=build
LD_PRELOAD=build/libfake_acl.so build/event_sequence_smoke
# interpose 需 fake 在下层：
LD_PRELOAD=build/libacl_event_trace.so:build/libfake_acl.so build/event_sequence_smoke
```

## 容器

```bash
./build.sh ascend
```

环境变量：

- `ACL_EVENT_TRACE_DIR`：输出目录
- `ACL_EVENT_TRACE_CAPACITY`：槽位数（默认 65536）
- `RANK` / `LOCAL_RANK`

Finalize：`acl_event_trace_finalize()` 或训练脚本 `finally`。
