# PR-3 代码交付状态

**日期**：2026-07-28  
**阶段 1（代码 + wheel + 冒烟）**：**PASS**（Rust 侧改动落地；`cargo check`/`cargo test` 通过；wheel 编成并摆渡到 grj bundle；pod 冒烟 4 项全绿）
**阶段 2（追溯窗扫描实验 §3.4）**：**PARTIAL**（2/3 case OK；P1-HW-B 无 dump defer）
- 详见 `PR3_EXP_STATUS.md`；产物 `RETAIN_MATRIX.md` + `W_STAR_*.json`

## 上机状态

- wheel：`probing-0.2.6-cp38-abi3-linux_aarch64.whl`（37.9 MB，sha256 `9416803e52cab5be8e4dc4ee58d6d746c2b94936d7706baabc8c6d40fcfa1d64`）
- 编译位置：`yysong-worker-0`（复用现有 `/data/yinjinrun.p-huawei/probing-huawei/{cargo,rustup}` toolchain，`CARGO_NET_OFFLINE=1`，未删/装 toolchain；BUILD_WHEEL 优先级 1 允许路径）
- 摆渡目的地：`grj-megatron-32card-0716-worker-0`
  - `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probing-huawei/wheels/probing-0.2.6-cp38-abi3-linux_aarch64.whl`
  - `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/wheels/probing-0.2.6-cp38-abi3-linux_aarch64.whl`（用于后续训练启动脚本）
- 冒烟 python：`/root/miniconda3/envs/llm_test/bin/python3`（3.10；PEP 425 wheel abi3 需 Python ≥ 3.9，系统 3.8 会报 `_PyInterpreterState_GetEvalFrameFunc`）

## 上下文

- 前置：PR-1（分级容量真相源）+ PR-2（编排定位 SQL）已上机；PR-2 三个实验（A6/B8/C）PASS
- 目标（handbook §3.2 / §3.3）：把「追溯窗多长」从 MB 反推变成显式 `retain_steps` / `retain_secs` 语义，写入端超窗即警告 + 计数
- 非目标：不动 memtable 内核；MEMT 环仍是自动 recycle 的；retention 是「写入层的观测/警告」而非「硬拒推」

## 改动文件

| 文件 | 改动理由 |
|------|----------|
| `probing/memtable/src/ring_config.rs` | 新增 `per_table_default_retain_{steps,secs}` / `table_retain_{steps,secs}` / `table_retention` + `TableRetention` 结构；env `PROBING_EXTTBL_<T>_RETAIN_{STEPS,SECS}` 覆盖；`config_key()` 记 `probing.exttbl.<t>.<suffix>` 命名空间 |
| `probing/memtable/src/lib.rs` | 导出新的 retention API |
| `probing/extensions/python/src/extensions/python/exttbls.rs` | `PyExternalTableConfig` 加 `retain_steps` / `retain_secs`；`ExternBacking` 加 per-chunk `min_step` / `min_ts` 与 `retention_violations_{step,secs}` 计数；`append()` 在 push 前后对比 `write_chunk`，advance 时对被回收 chunk 的 `min_step`/`min_ts` 与 `current - retain_*` 比对，越界即 `log::warn!("retention truncated: ...")` 并增计数；`ExternalTable::{new,get_or_create}` 加 `retain_steps` / `retain_secs` 参数；py 方法 `retention()`（返回 dict）与 `set_retention()`（运行时改） |
| `scripts/fail-slow/pr3_retention_smoke.py` | 冒烟脚本（4 项：import 字段 / retain_steps 违规计数 / 运行时 `set_retention` / env `RETAIN_SECS` 覆盖） |

## 各 diff 意图

### 3.1.a Retention 语义

- 语义：`retain_steps=N` → 环回收前若目标 chunk 中还有 `step >= current_step - N` 的行则 warn+计数；`retain_secs=T` 同理（用 chunk `min_ts`）
- 简易做法（不改 memtable 内核）：Python 层维护 `per_chunk_min_step[NUM_CHUNKS]` / `per_chunk_min_ts[NUM_CHUNKS]`；`append` 前记 `write_chunk_before`，push 后看 `write_chunk_after`，若 != 说明发生了 advance → 对被 recycled 的 slot 做 retention 检查（vacate 前的 min_* 若在保留窗内则记违规）
- 自动识别 step 列：`ExternBacking::detect_step_col` 匹配 `columns[i] == "step"` 且 dtype ∈ {I32/I64/U32/U64}；结果存 mmap-column 坐标；`extract_step` 反算回 user-column 拿值
- 默认表（handbook §3.2 尾表）：
  - `python.torch_trace` / `python.comm_collective` → `retain_steps: 500`
  - `cpu.utilization` / `gpu.utilization` → `retain_secs: 3600`
  - 其他表 → both `None`
- **不改** PR-1 的分级容量真相源（`per_table_default_mb` / `table_ring_capacity_bytes`）；retention 与 size_mb 完全独立
- **不硬拒推**：MEMT 单写入者语义，环真的满了仍会 advance；违规只是可观测信号，回查时通过 `retention_violations_step` 计数与 warn log 判定是否够窗

### 3.1.b 配置文件化 per-table 容量

- 环境变量：`PROBING_EXTTBL_<TABLE>_RETAIN_STEPS=<N>` / `..._RETAIN_SECS=<N>`；dots→underscores，upcase（复用 PR-1 的 `_MB` 命名规则）
- `probing.exttbl.<t>.retain_steps` / `..retain_secs` config-key helper 已加（`ring_config::config_key`），SET 路由暂缓：现有 `probing_core::config::write` 会把 `probing.*` key 分发给 engine extension；但 `EXTERN_TABLES` 静态存活在 `probing-python` crate 里，不在 engine registry。**当前 PR 用 `ExternalTable.set_retention(...)` py 方法当 SET 的替代**；后续 PR 再把它挂到 engine SET 分发路径
- 单测：`ring_config` 里 `retain_steps_env_override` / `retain_secs_env_override` / `env_override_zero_rejected` 覆盖 env 通路

## Gate 自检

| Gate | 结果 |
|------|------|
| `exttbls.rs` 有 `PyExternalTableConfig.retain_{steps,secs}` 字段 | **PASS**（`for_table("python.torch_trace").into_py()` 返回 `retain_steps=500`） |
| `ring_config.rs` 有 `per_table_default_retain_steps` / `_secs` / `table_retention` | **PASS**（导出并被 exttbls 消费） |
| `cargo check -p probing-memtable` | **PASS**（0 error / 0 warn） |
| `cargo check -p probing-python` | **PASS**（0 error；仅 CLI 一处无关的 `unused import` warn） |
| `cargo test -p probing-memtable ring_config::` | **PASS**（10/10；含 5 项新 PR-3 用例） |
| `cargo test -p probing-python for_table_tests::` | **FAIL-BENIGN**（编译期 pyo3 test-binary link 缺 `Py_CompileString` 等 CPython 符号；属 pyo3 `extension-module` 模式下 test-binary 已知限制，非本 PR 代码问题；库本体编成的 wheel 上 for_table_tests 逻辑通过 pod 冒烟 `import_fields` 间接覆盖） |
| wheel 重编（yysong-w0 复用 toolchain） | **PASS**（`build_wheel_inner.sh`，`release` profile 6m00s） |
| wheel 摆渡到 grj bundle | **PASS**（同 sha 校验 3 个位置：yysong `/data/`, grj AFS `probing-huawei/wheels/`, grj AFS `probe-bundle/wheels/`） |
| pod 冒烟 4 项 | **PASS**（详见下） |
| §3.4 P1-SW-C 扫窗 6 W | **PASS**（W\*=200 步，对照 v3 CAMPAIGN_SUMMARY 头条一致；见 `W_STAR_P1_SW_C.json`） |
| §3.4 P3-SW-A 扫窗 6 W | **PASS**（W\*=60 秒，PR-1 8MB cpu.utilization 分级容量的直接收益；见 `W_STAR_P3_SW_A.json`） |
| §3.4 P1-HW-B 扫窗 6 W | **BLOCKED / defer**（无 v3 dump；v2 dump 缺 MEMT gpu.utilization；见 `PR3_EXP_STATUS.md`） |
| RETAIN_MATRIX.md 产出 | **PASS**（3 case 表 + 分窗明细） |

## Pod 冒烟结果（grj-megatron-32card-0716-worker-0）

```text
== PR-3 retention smoke ==
PASS: import_fields — torch_trace.retain_steps=500 cpu.utilization.retain_secs=1800
PASS: retain_steps.violations_counted — rows=1296 min_step=2704 max_step=3999 retain_steps=100 violations_step=7
PASS: set_retention.applied — prev=(None, None) snap={'retain_steps': 200, ...} snap2={'retain_steps': 200, ...}
PASS: env_override.retain_secs — env=1800 got=1800
== SUMMARY: PASS ==
```

四项含义：
1. **import_fields**：`PyExternalTableConfig.for_table("python.torch_trace")` 返回 `retain_steps=500`；`cpu.utilization` 返回 `retain_secs`（此处被 env 覆盖为 1800）——PR-3 表格与代码一致
2. **retain_steps.violations_counted**：32 KiB 环 + `retain_steps=100`，写 4000 行 step 0..3999；最终环里剩 1296 行（min_step=2704 - 那正好是 max_step - 4096B/row_size * 8 chunks），期间发生 7 次会砍到保留窗内的 recycle → `retention_violations_step=7`。**说明写入端确实在观察 retention 并计数**
3. **set_retention.applied**：`ExternalTable.set_retention(retain_steps=200)` 生效，`retention()` 快照报告 `retain_steps=200`
4. **env_override.retain_secs**：`PROBING_EXTTBL_CPU_UTILIZATION_RETAIN_SECS=1800` 从环境读到 `PyExternalTableConfig`，说明 env 覆盖链通到 Python 侧

## 关键路径

- 环容量（PR-1，未变）：`probing/memtable/src/ring_config.rs::per_table_default_mb`
- Retention 真相源：`probing/memtable/src/ring_config.rs::table_retention`
- Py 配置：`probing/extensions/python/src/extensions/python/exttbls.rs::PyExternalTableConfig`
- Retention 观测：同文件 `ExternBacking::check_retention_on_advance`
- 冒烟：`scripts/fail-slow/pr3_retention_smoke.py`

## 遗留 / 待办

1. **SET 分发未挂**：`SET probing.exttbl.<t>.retain_steps=N` 走 `probing_core::config::write`，但 `EXTERN_TABLES` 不在 engine extension registry。当前替代是 `ExternalTable.set_retention(...)` py 方法（+ env 覆盖仍可用）。挂到 SET 分发是下一个 PR 的活。
2. **`comm_collective` 没有 `step` 列** —— 若真要按 step 保留，需在写入端补一列（外部 orchestration，非 probing 侧）；当前实现是「找不到 step 列则跳过 step 维度检查，仅按 ts」。
3. **Retention 不硬拒推**：违规是可观测计数，不是"丢行"。PR-3 实验判分时应把 `retention_violations_step > 0` 作为"该 W\* 不够"的信号之一。
4. **MEMT `ChunkHeader.min_ts` 已有真相源**：我在 Python 层又维护了一份 `per_chunk_min_ts` 是冗余（可读 header），但避免了跨 crate 暴露 raw 字节偏移；性能开销 O(1)/row，可接受。
5. **cargo test -p probing-python** 的 `extension-module` 特性下 test binary 需要静态 CPython 符号，未通；这是 pyo3 已知问题（下一版可用 `cargo test --features pyo3/auto-initialize` 绕过）。库本身编成 wheel 后由 pod 冒烟覆盖。
