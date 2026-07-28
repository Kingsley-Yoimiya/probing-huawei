# PR-1 代码交付状态

**日期**：2026-07-27  
**状态**：**PARTIAL**（代码已落地并上 pod 编 wheel；§1.5 健康长 run 已发射，验收待 dump 后）

## 上机状态（2026-07-27 17:00）

- wheel：`probing-0.2.6` @ yysong-w0（maturin + 已有 toolchain）
- 健康 run：`20260727_165856-yjr-as-b-pr1-health` RUNNING → 见 `PR1_BASELINE_LAUNCH.md`
- 热修：`exttbls.rs` `config_for_table`；`torch_probe.py` `shadow_step_in_cycle` 函数头

---

## 改动文件

| 文件 | 改动 |
|------|------|
| `probing/memtable/src/ring_config.rs` | **新增** 分级环容量真相源：`per_table_default_mb` / `table_ring_capacity_bytes` / `table_mmap_chunk_layout`；`PROBING_EXTTBL_<TABLE>_MB` 覆盖 |
| `probing/memtable/src/lib.rs` | 导出 `ring_config` |
| `probing/extensions/python/src/extensions/python/exttbls.rs` | `PyExternalTableConfig::for_table`；`ExternalTable` 默认 `discard_threshold=None` → 走 `for_table`；单元测 `for_table_sets_tiered_defaults` |
| `probing/extensions/cc/src/extensions/cpu/collector.rs` | `cpu_mmap_ring_config(table)` 委托 `ring_config`（保留 `PROBING_CPU_RING_MB` 等 legacy） |
| `probing/extensions/gpu/src/extensions/collector.rs` | `gpu.utilization` 环 32KiB → **8MiB** |
| `probing/extensions/gpu/src/extensions/hccs_collector.rs` | `gpu.hccs` 环 32KiB → **4MiB** |
| `python/probing/profiling/torch_probe.py` | rate=0 稀采锚点（`PROBING_TORCH_MIN_STEP_INTERVAL`，默认 500）；`Variables` 懒创建表（默认不写 `python.variables`） |
| `python/probing/tracing/backends.py` | `PROBING_SPAN_BACKENDS` 默认 `none` |
| `src/lib.rs` | 导出 `PyExternalTableConfig` 到 Python |
| `tests/unit/probing/tracing/test_span_backends.py` | 默认 backend 改为空 |
| `tests/regression/profiling/test_torch_probe_sampling.py` | rate=0 稀采用例 |

---

## 各 diff 意图

### 1.1.a 关键小表环形分级扩容

**排查结论（grep 已做）**：
- `cpu.utilization` / `cpu.tasks` **不走** `exttbls.rs` 的 `discard_threshold`，而在 `cc/.../cpu/collector.rs` 用 `ExposedTable::create` 直接定环。
- 本分支 CPU 侧此前已升到 8MiB（`PROBING_CPU_RING_MB` / 1MiB×8 chunks）；**真正仍偏小的是 GPU**：`gpu.utilization` / `gpu.hccs` 原为 `4096×8=32KiB`（落盘只剩数秒），与 v2 UNRESOLVED 现象一致。
- **未发现** `cpu.utilization` 在 exttbls 层被盖成 64KB；小环根因在 **GPU collector 硬编码 4KiB chunk**。

**实现**：
- 共享 `ring_config.rs`：`cpu.*` / `gpu.utilization` → 8MB；`gpu.hccs` → 4MB；其余 Python 外表默认 20MB。
- `PyExternalTableConfig::for_table(name)` + `PROBING_EXTTBL_CPU_UTILIZATION_MB` 等 env。
- `ExternalTable::new/get_or_create` 未显式传 `discard_threshold` 时自动 `for_table(name)`。

### 1.1.b rate=0 稀采兜底

- `rate <= 0` 时 `_sample_period()` → `PROBING_TORCH_MIN_STEP_INTERVAL`（默认 **500**）。
- `_ensure_step_plan()` 按 period 做分层采样（step 0、500、1000… 有 `sampled_step=True`），给追溯窗 onset 前锚点。
- 与 v2「rate=0 完全无 torch_trace」对比：常驻仍极稀，但环内不再从 step~189 才开始。

### 1.1.c 默认关噪音大表

- `PROBING_SPAN_BACKENDS` 默认 **`none`** → 不写 `python.trace_event`。
- `Variables` 去掉 `@table` 自动建表；仅 `vars=`/`exprs=` 配置且 `trace_variables()` 实际写行时才 `ExternalTable("variables", …)`。
- 显式恢复：`PROBING_SPAN_BACKENDS=memtable`；`probing.torch.profiling=on,vars=x@fn`。

---

## Gate 自检

| Gate | 结果 |
|------|------|
| `exttbls` 有 `for_table` / 分级默认 | ✅ `grep for_table` + `ring_config.rs` |
| rate=0 稀采逻辑可读 | ✅ `_min_step_interval` + `_ensure_step_plan` |
| 默认不启用 trace_event / variables | ✅ backends 默认 `none`；Variables 懒表 |
| `cargo test` | ⚠️ 本机 **无 cargo**，未跑；逻辑有 `ring_config` / `for_table` 单测源码 |
| pytest | ⚠️ 无已编 `probing._core`；**内联 smoke**（`parse_backend_names`、`rate=0` 周期）通过 |

---

## 短 smoke 结果

```text
# 内联 Python（无需 wheel）
backends.parse_backend_names() == []           # 默认 none
PROBING_SPAN_BACKENDS=memtable → ['memtable']
rate=0 @ interval=500 → sampled steps [0,500,1000]
rate=0 @ PROBING_TORCH_MIN_STEP_INTERVAL=100 → [0,100,200,300,400]
ring_config.rs 含 cpu.utilization=8MB, gpu.hccs=4MB
→ smoke_ok
```

---

## 是否可开健康长 run 验收

**可以派 PR-1 baseline 长跑**（前提：pod 内 `maturin build` / 装新 wheel 后再跑）。

建议验收臂（手册 §1.5）：
- 健康长 run ≥1h，`PROBING_TORCH_PROFILING=on,rate=0`（或 case 常驻配方）
- dump 后查：`cpu.utilization` / `gpu.utilization` 时间跨度；`torch_trace` 行数 ≥ steps/500；无 `python.trace_event` / `python.variables` 目录；单 pid 体积 ≤70MB

---

## 遗留问题

1. **本机未编 wheel**：需在 `yysong-worker-0`（或本机有 Rust 链）跑 `cargo test -p probing-memtable -p probing-cc -p probing-python` 与相关 pytest。
2. **rate=0 稀采步仍为「整步 sampled」**：一步可能多行 module 行（手册验收只要求 ≥steps/500 行，已满足）；若需严格「每 N 步 1 行」需后续 PR 收窄 hook。
3. **`PER_TABLE_DEFAULTS` 名为 `per_table_default_mb()`**（函数表，非 `HashMap` 常量）；语义与手册一致。
4. **CPU legacy env**（`PROBING_CPU_RING_MB`）仍优先于 `PROBING_EXTTBL_*`；与既有 P-FIX 脚本兼容。

---

## 关键路径

- 环容量：`probing/memtable/src/ring_config.rs`
- Python 外表：`probing/extensions/python/src/extensions/python/exttbls.rs` → `for_table`
- GPU 小表：`probing/extensions/gpu/src/extensions/collector.rs`、`hccs_collector.rs`
- 稀采：`python/probing/profiling/torch_probe.py`
- 关 trace：`python/probing/tracing/backends.py`
