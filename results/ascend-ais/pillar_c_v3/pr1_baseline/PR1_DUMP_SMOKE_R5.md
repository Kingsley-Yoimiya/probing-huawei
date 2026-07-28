# PR-1 Dump 短 Smoke · pid-fix 复验（r5 · r2 同参）

**run_id**：`20260727_204500-yjr-as-b-pr1-health-smoke-r5b`  
**日期**：2026-07-27  
**状态**：**DONE** — 门闩 ✅；`trace_event` ✅；`torch_trace` **n=2549** ✅；attach **worker rank 7** ✅

> r5 首次尝试（`1500/100`）因初版 pid 过滤读 `ps` 命令行 `LOCAL_RANK`（昇腾 worker 仅在 environ）导致 `attach=no`；修正为 `hold_exec` 同款 `/proc/$pid/environ` + 排除 `torchrun` 后，r5b 通过。

---

## 代码修复

| 文件 | 变更 |
|------|------|
| `project/probing-test/scripts/fail-slow/platform/ascend/dump_probing_sql.sh` | `candidate_pids` 排除 `torchrun`/`distributed/run.py`；`LOCAL_RANK` 从 `/proc/$pid/environ` 读取；优先 victim rank + `python.torch_trace` shm；manifest 增 `pid_role` |
| `project/probing-test/scripts/fail-slow/dump_probing_sql.sh` | 同上（非 ascend 副本对齐） |

**sync**：hold_exec jsync → `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/dump_probing_sql.sh`

---

## 发射配方（r2 同参 + pid-fix）

| 项 | 值 |
|----|-----|
| pod | `yysong-worker-0` |
| 臂 | `C2_probing`，`INJECT_KIND=none` |
| `ITERS` / `WARMUP` | **3000** / **20** |
| `DUMP_WAIT_S` | **240** |
| `PROBING_TORCH_PROFILING` | `on,rate=0` |
| `PROBING_TORCH_MIN_STEP_INTERVAL` | **100** |
| `PROBING_SPAN_BACKENDS` | `none` |

---

## 训完状态

| 项 | 结果 |
|----|------|
| jsonl | ✅ 16/16 rank × **3000** 行 |
| dump 时刻 | ts=**20:53:00** |
| hold_exec | `waiting 240s for SQL dump (no inject)` → `SQL dump attempted` ✅ |

---

## 门闩（no-inject dump 路径）

| # | 检查项 | 结果 |
|---|--------|------|
| G1 | `waiting 240s for SQL dump (no inject)` | ✅ |
| G2 | `…/C2_probing/probing/` 存在 | ✅ |
| G3 | `query_manifest.json` `attach=ok` | ✅ pid=**2251159** `pid_role=worker:local_rank=7` |
| G4 | `SQL dump attempted` | ✅ |

---

## 硬验收

### 1. `python.trace_event` — **PASS**

| 观测 | 结果 |
|------|------|
| `SHOW TABLES` | **无** `trace_event` 行 |
| 旁证 | 仅 `python.torch_trace` / `torch_step_timing` 等 |

### 2. `python.torch_trace` — **PASS（n=2549）**

| 观测 | 结果 |
|------|------|
| `COUNT(*)` | **n=2549**, gmin=0, gmax=1302 |
| attach pid | **2251159** = **worker LOCAL_RANK=7**（非 torchrun 父进程） |
| 门槛 | n≥30 ✅（理论 interval=100 @ ~1500 step → ≥15；实测远超） |

### 3. 与 r4 对照

| 轮次 | attach pid | pid_role | n |
|------|-----------|----------|---|
| r4 | 1880503 | torchrun 父进程 | **0** |
| r5b | 2251159 | worker rank 7 | **2549** |

---

## §1.5 快照

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | cpu.utilization | ✅ |
| 2 | gpu.utilization | ✅ |
| 3 | torch_trace 有行 | ✅ **n=2549** |
| 4 | trace_event 不存在 | ✅ |
| 5 | variables 不存在 | ✅ |
| 6 | attach 非 launcher | ✅ `worker:local_rank=7` |

---

## 路径

| 用途 | 路径 |
|------|------|
| AFS | `…/pr1_baseline/20260727_204500-yjr-as-b-pr1-health-smoke-r5b/` |
| dump | `…/C2_probing/probing/` |
| 本机 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_204500-yjr-as-b-pr1-health-smoke-r5b/` |

---

## 结论

**DONE** · **torch_trace n=2549** · **pid_role=worker:local_rank=7**

- **trace_event**：**PASS**
- **torch_trace 行数**：**PASS**（pid-fix 生效）
- **下一步**：可进入 PR-1 正式 ≥1h 健康长跑

---

*验收：Pillar C v3 PR-1 pid-fix · 2026-07-27*
