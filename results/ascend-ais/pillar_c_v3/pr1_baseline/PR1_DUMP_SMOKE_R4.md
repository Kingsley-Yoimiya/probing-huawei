# PR-1 Dump 短 Smoke · fix2 复验（r4 · r2 同参）

**run_id**：`20260727_202046-yjr-as-b-pr1-health-smoke-r4`  
**日期**：2026-07-27  
**状态**：**PARTIAL** — 门闩 ✅；`trace_event` ✅；`torch_trace` SQL 链 ✅、**行数仍 0（n=0）**

---

## 前置确认

| 项 | 结果 |
|----|------|
| `yysong-worker-0` IDLE | ✅ 发射前无活 `torchrun` |
| pydeps `TraceEvent` 懒注册 | ✅ `_ensure_table()` on first write，无 `@table` import 建环 |
| dump 脚本 `global_step` + COUNT | ✅ bundle 已 jsync |

---

## 发射配方（r2 同参 + fix2）

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
| jsonl | ✅ 16/16 rank × **3000** 行（step 0–2999） |
| dump 时刻 | ts=**20:26:30**（warmup 20 s + wait 240 s ≈ 260 s 入训） |
| 理论锚点数 | ~1500 step @ dump；interval=100 → **≥15** 条/rank |
| hold_exec | pull 时 tar 截断（rank jsonl 大文件），probing dump 完整 |

---

## 门闩（no-inject dump 路径）

| # | 检查项 | 结果 |
|---|--------|------|
| G1 | `waiting 240s for SQL dump (no inject)` | ✅ |
| G2 | `…/C2_probing/probing/` 存在 | ✅ |
| G3 | `query_manifest.json` `attach=ok` | ✅ pid=**1880503** |
| G4 | `SQL dump attempted` | ✅ |

---

## 硬验收

### 1. `python.trace_event` — **PASS**

| 观测 | 结果 |
|------|------|
| `SHOW TABLES` | **无** `trace_event` 行 |
| shm 旁证（victim pid） | 无 `python.trace_event` 文件 |

### 2. `python.torch_trace` — **FAIL（n=0）**

| 观测 | victim pid `1880503` | worker pids `1880896–1880911` |
|------|---------------------|------------------------------|
| `COUNT(*)` @ dump | **n=0**, gmin=gmax=0 | 训后进程已退出，无法 CLI 复查 |
| shm `python.torch_trace` | **无** | **16/16 × 21 MiB** 热环 |
| `SHOW TABLES` | 表可注册但空 | — |

**dump.log**：`pid=1880503 attach=ok` — 该 pid 为 **torchrun 父进程**（`distributed/run.py`），非 rank worker。

### 3. 多 rank 旁证（训后 shm 残留）

```
/dev/shm/probing/1880896/python.torch_trace  … 20972800 B
…（16 个 worker pid 均有 21 MiB 环）
/dev/shm/probing/1880503/  → cpu/gpu/comm_collective，**无** torch_trace
```

---

## 根因判定

**victim-only attach 误命中 torchrun 父进程**，不是 rate=0 未写、也不是 federation 全局坏：

1. **写入侧正常**：16 个 worker 均有 21 MiB `python.torch_trace` shm 环（与 r2 旁证一致）。
2. **dump 侧查错 pid**：`dump_probing_sql.sh` `candidate_pids()` 匹配 `/tmp/tbp_npu.py`，torchrun 父进程也命中且 `probing -t` 可 ping（`attach=ok`），但该进程**不跑训练 hook**，环内无 `torch_trace` 行 → `COUNT(*)=0`。
3. **非 federation 全断**：同一窗 `cpu.utilization` / `gpu.utilization` 在父 pid 可查且有数据；`torch_trace` 仅存在于 worker shm。
4. r3c 同类：`attach=ok` + `n=0`，部分轮次 victim shm 无环、他 rank 有环 — 同一选择逻辑的不稳定表现。

**建议修复**：`candidate_pids` 排除 `torch.distributed.run` / 无 `LOCAL_RANK` 的 launcher；或优先 `LOCAL_RANK` 明确的工作进程；或对首个有非空 `python.torch_trace` shm 的 pid 做 COUNT。

---

## §1.5 快照

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | cpu.utilization | ✅ |
| 2 | gpu.utilization | ✅ |
| 3 | torch_trace 有行 | ❌ **n=0**（查错 pid） |
| 4 | trace_event 不存在 | ✅ |
| 5 | variables 不存在 | ✅ |
| 6 | 单 pid 体积 | ⚠️ victim ~28 MiB；worker 单 pid ~21 MiB 仅 torch_trace 环 |

---

## 路径

| 用途 | 路径 |
|------|------|
| AFS | `…/pr1_baseline/20260727_202046-yjr-as-b-pr1-health-smoke-r4/` |
| dump | `…/C2_probing/probing/` |
| 本机 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_202046-yjr-as-b-pr1-health-smoke-r4/` |

---

## 结论

**PARTIAL** · **torch_trace n=0**

- **trace_event**：**PASS**
- **torch_trace 行数**：**FAIL**（采集有环 / SQL 查父进程空表）
- **下一步**：修 `dump_probing_sql.sh` pid 选择 → 同参复验 `COUNT(*)≥15`

---

*验收：Pillar C v3 PR-1 r2-retest · 2026-07-27*
