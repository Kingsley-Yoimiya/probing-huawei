# PR-1 Dump 短 Smoke · fix2 验收（r3）

**run_id（有效）**：`20260727_195500-yjr-as-b-pr1-health-smoke-r3b` / `20260727_200200-yjr-as-b-pr1-health-smoke-r3c`  
**无效对照**：`20260727_194500-yjr-as-b-pr1-health-smoke-r3`（`ITERS=800` + `DUMP_WAIT_S=180` → 训完才 dump，`attach=no`）  
**日期**：2026-07-27  
**状态**：**PARTIAL** — 门闩 ✅；`trace_event` ✅；`torch_trace` SQL 链 ✅、**行数仍 0**

---

## 本轮代码修复

| 项 | 文件 | 改动 |
|----|------|------|
| dump SQL 列名 | `probing-test/.../platform/ascend/dump_probing_sql.sh` | `step` → `global_step`；新增 `torch_trace_count` 探针；manifest 以 COUNT 成功辅助判 present |
| trace_event 懒注册 | `python/probing/tracing/table.py` | 去掉 `@table` 装饰器即时 `init_table()`；仅 MemtableBackend 写时建环 |
| collective 镜像 | `profiling/collective/record.py` | `write_trace_event` 前检查 `persistence_enabled()` |
| collective 默认 | `profiling/collective/config.py` | `trace_event` 默认跟随 `persistence_enabled()`（`none` → 不写） |
| hold_exec 透传 | `hold_exec_run_case.sh` | `PROBING_TORCH_MIN_STEP_INTERVAL` 透传到训练 env |

**根因（trace_event）**：`@table` 在 import 时调用 `init_table()`，即使 `PROBING_SPAN_BACKENDS=none` 也会预分配 ~21 MiB `python.trace_event` 并出现在 `SHOW TABLES`。与 backends 默认 `none` 无关，是表注册时机问题。

**sync**：`table.py` / `collective/*.py` → pod `probe-bundle/pydeps`；`dump_probing_sql.sh` → `probe-bundle/`（hold_exec jsync）。

---

## 发射配方（r3b / r3c）

| 项 | r3b | r3c |
|----|-----|-----|
| pod | `yysong-worker-0` | 同左 |
| `ITERS` / `WARMUP` | 800 / 20 | 1500 / 20 |
| `DUMP_WAIT_S` | **60**（800 step 训程 ~135s，180s 会训完） | **100** |
| `PROBING_TORCH_PROFILING` | `on,rate=0` | 同左 |
| `PROBING_TORCH_MIN_STEP_INTERVAL` | 100 | 同左 |
| `PROBING_SPAN_BACKENDS` | `none` | 同左 |

> 用户原配方 `ITERS=800 DUMP_WAIT_S=180` 在 r3 已证伪：`attach=no`。短 ITERS 须 **缩短** `DUMP_WAIT_S`（≤60–100s）或 **加长** ITERS（≥2000）。

---

## 门闩（r3b / r3c）

| # | 检查项 | r3b | r3c |
|---|--------|-----|-----|
| G1 | `waiting …s for SQL dump (no inject)` | ✅ 60s | ✅ 100s |
| G2 | `probing/` 目录 | ✅ | ✅ |
| G3 | `attach=ok` | ✅ pid=1592375 | ✅ pid=1747882 |
| G4 | `SQL dump attempted` | ✅ | ✅ |

---

## 硬验收两项

### 1. `python.trace_event` — **PASS**

| 观测 | r2（修前） | r3b / r3c（修后） |
|------|-----------|------------------|
| `SHOW TABLES` | 含 `python.trace_event`（global+probe） | **无** `trace_event` 行 |
| shm 旁证（victim pid） | ~21 MiB 热环 | **无** `python.trace_event` 文件 |
| `PROBING_SPAN_BACKENDS` | `none`（已透传） | 同左 |

### 2. `python.torch_trace` — **PARTIAL（SQL ✅ / 行数 ❌）**

| 观测 | r2（修前） | r3b / r3c（修后） |
|------|-----------|------------------|
| tail SQL | `No field named step` | **`global_step` 列查询成功**（`torch_trace_tail\|ok`） |
| `COUNT(*)` | 未可靠执行 | **查询成功**，`n=0`，`gmin=gmax=0` |
| shm | 21 MiB 环（旁证） | r3b victim 有 `torch_trace` 环；r3c victim 无（其他 rank pid 有） |
| manifest `python.torch_trace` | false（列名 bug） | true（COUNT 探针 ok，**不代表有行**） |

**判定**：dump 验收 SQL 链已闭环；**尚不能勾「有行」**。与 r2 类似，可能存在 **mmap 有环 / SQL 空表** 的 federation 间隙，或短跑 + victim pid 未命中写环的 rank。建议下轮用 r2 同参（`ITERS=3000 DUMP_WAIT_S=240`）+ 修后 wheel 复验 `COUNT(*)≥steps/interval`。

---

## §1.5 快照（r3c）

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | cpu.utilization | ✅ 有数据 |
| 2 | gpu.utilization | ✅ 有数据 |
| 3 | torch_trace 有行 | ❌ `n=0` |
| 4 | trace_event 不存在 | ✅ |
| 5 | variables 不存在 | ✅（SHOW 无） |
| 6 | 单 pid 体积 ≤70 MiB | ✅ 粗算 ~49 MiB（无 trace_event；r3c victim pid 1747882） |

---

## 路径

| 用途 | 路径 |
|------|------|
| r3b AFS | `…/pr1_baseline/20260727_195500-yjr-as-b-pr1-health-smoke-r3b/` |
| r3c AFS | `…/pr1_baseline/20260727_200200-yjr-as-b-pr1-health-smoke-r3c/` |
| 本机 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/<run_id>/` |

---

## 结论

**PARTIAL**

- **trace_event**：**PASS**（懒注册 + collective 不写镜像）
- **torch_trace**：SQL **PASS**；**行数 FAIL**（`COUNT(*)=0`）

---

*验收：Pillar C v3 PR-1 fix2 · 2026-07-27*
