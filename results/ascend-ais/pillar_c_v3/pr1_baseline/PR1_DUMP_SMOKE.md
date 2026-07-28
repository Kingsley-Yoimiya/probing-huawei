# PR-1 Dump 短 Smoke · 发射与验收（r2 终态）

**run_id**：`20260727_190900-yjr-as-b-pr1-health-smoke-r2`  
**日期**：2026-07-27  
**状态**：**PARTIAL** — 门闩 **PASS**；§1.5 部分通过（`torch_trace` / `trace_event` / 体积仍 FAIL）

> r1 `20260727_182522-yjr-as-b-pr1-health-smoke`（`DUMP_WAIT_S=1200`）仅作门闩对照，**不以之为终态**。本报告以 r2 为准。

---

## 背景

长跑 `20260727_165856-yjr-as-b-pr1-health` 训完但无 probing dump（`INJECT_KIND=none` 时 dump 绑在注入门闩内）。`hold_exec_run_case.sh` 已修：warmup 后对 C2+none 走独立 dump 路径。本 smoke 专验该路径 + 手册 §1.5 硬条件（缩参，非 1h 正式长跑）。

---

## 发射配方（r2）

| 项 | 值 |
|----|-----|
| pod | `yysong-worker-0` |
| 臂 | `C2_probing`，`INJECT_KIND=none` |
| `ITERS` / `WARMUP` | 3000 / 50 |
| `DUMP_WAIT_S` | **240**（相对 r1 的 1200 缩短，确保 dump 时训练仍在） |
| `PROBING_TORCH_PROFILING` | `on,rate=0` |
| `PROBING_GPU/CPU_SAMPLE_MS` | 500 / 500 |
| `PROBING_SPAN_BACKENDS` | `none`（显式关 trace_event） |
| wheel | probing-0.2.6（pod pydeps） |
| 脚本 | `hold_exec_run_case.sh`（no-inject dump + `PROBING_SPAN_BACKENDS` 透传） |

## 路径

| 用途 | 路径 |
|------|------|
| AFS 根 | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_190900-yjr-as-b-pr1-health-smoke-r2/` |
| C2 out | `…/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/` |
| dump | `…/C2_probing/probing/` |
| 本机 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_190900-yjr-as-b-pr1-health-smoke-r2/` |
| hold_exec 日志 | `…/logs/hold_exec.log` |

---

## 训完状态

| 项 | 结果 |
|----|------|
| jsonl | ✅ 16/16 rank × **3000** 行（step 0–2999） |
| 训程 | ~**498 s**（rank0 ts 跨度） |
| `node_0.log` | `DONE world=16` + `PARTIAL_DONE`（hold_exec 剂量窗 [100,300] 语义；**无** `node_0.done` 标记文件） |
| hold_exec 终态 | `DONE rc=0` @ 19:39 CST |
| pod 现状 | IDLE（`tbp_npu` 已退出） |

dump 时刻（`query_manifest.json` ts=**19:35:30**）约在训程 **~260 s**（warmup 20 s + `DUMP_WAIT_S` 240 s），当时 step≈1500；训程后续继续至 2999。

---

## 门闩验收（no-inject dump 路径）

| # | 检查项 | 结果 | 证据 |
|---|--------|------|------|
| G1 | `hold_exec.log` 含 `waiting …s for SQL dump (no inject)` | **PASS** | `waiting 240s for SQL dump (no inject)…` |
| G2 | `…/C2_probing/probing/` 存在 | **PASS** | 目录 20 文件，含 `query_manifest.json` |
| G3 | `query_manifest.json` `attach=ok` | **PASS** | pid=1282373，victim_local_rank=7 |
| G4 | `SQL dump attempted` 行 | **PASS** | hold_exec + `probing_dump.log` |

**门闩结论：PASS**（修后的 C2+none 独立 dump 路径本轮验证通过）。

---

## §1.5 检查表（手册 PILLAR-C-V3 §1.5；短跑放宽时间跨度）

| # | 检查项 | 期望（正式） | 短跑口径 | 结果 | 说明 |
|---|--------|-------------|----------|------|------|
| 1 | `cpu.utilization` 有数据 / 跨度 | ≥1 h | 有数据即可 | **PASS** | `query_cpu_util.txt` 有行；采样桶 ~0.55 s（LIMIT 100 内） |
| 2 | `gpu.utilization` 有数据 / 跨度 | ≥1 h | 有数据即可 | **PASS** | `query_gpu_util.txt` 16 卡有 util%；ts 桶 span **~26 s** |
| 3 | `torch_trace` @ rate=0 稀采 | ≥ steps/500（本 run ≥6） | 同左 | **FAIL** | 见下节 |
| 4 | `python.trace_event` | ❌ 不存在 | 同左 | **FAIL** | `SHOW TABLES` 仍列出 `python.trace_event` |
| 5 | `python.variables` | ❌ 不存在 | 同左 | **PASS** | `SHOW TABLES` 无 `variables` |
| 6 | 单 pid 总体积 | ≤ 70 MB | 同左 | **FAIL** | shm 旁证见下 |
| 7 | `step_ms` vs 基线 | <1% | 未验 | **N/A** | 无同窗 C0；median **158.8 ms**（rank0） |

**§1.5 正式项：3/6 可勾（+1 N/A）→ smoke 总评 PARTIAL**

---

## `torch_trace` 细查

| 观测 | 内容 |
|------|------|
| `query_manifest` | `python.torch_trace: false`；`torch_trace_tail: error=query_rc_1` |
| `SHOW TABLES`（dump 窗） | **无** `python.torch_trace` 行 |
| `query_torch_trace_tail.txt` | SELECT 用了列名 `step`，但 schema 为 `global_step`/`local_step` → **dump 脚本列名过时** |
| 同错日志 | `node_0.log` / probing server：`No field named step` |
| shm 旁证（r2 时段 pid `1043536`） | 存在 **`python.torch_trace` 21 MiB 热环**（与 `rate=0` 一致有写入） |
| rate=0 锚点 | 默认 `PROBING_TORCH_MIN_STEP_INTERVAL=500`；dump@~260 s、~1500 step 理论应有 step 0/500/1000 锚点 |

**判定**：采集侧**可能有**稀采数据（shm），但 dump 验收链路 **未闭环**——manifest 误报 false + SQL 列名 bug。不能标 PASS。

---

## `trace_event` / `variables` / 体积

| 表 | SHOW TABLES | shm 旁证（r2 时段） |
|----|-------------|---------------------|
| `python.trace_event` | ✅ 存在（global + probe） | **21 MiB** 热环（`PROBING_SPAN_BACKENDS=none` 未生效或未阻止表注册） |
| `python.variables` | ❌ 不存在 | 未见 |
| 其他噪音 | — | `python.comm_collective` 21 MiB；`python.torch_step_timing` 21 MiB |

单 pid 热环加总（`ls`）：cpu×2 + gpu×2 + hccs + comm + timing + trace + trace_event ≈ **~91 MiB** → **超 70 MiB 门槛**。

---

## 关键日志摘录

```
FIRE_OK
  warmup ok (20s)
  waiting 240s for SQL dump (no inject)…
  dumping Probing SQL / host_psi…
  SQL dump attempted → …/C2_probing/probing/
  waiting done… t=60s jsonl=16
  waiting done… t=120s jsonl=16
  measure window complete + training gone → accept partial (step_300)
[hold-exec] DONE rc=0
```

---

## 最短复现建议（**不要**再开 1h 长跑）

1. **修 dump SQL**（阻塞项）：`dump_probing_sql.sh` 将 `step` 改为 `global_step`（或 `COUNT(*)` 探针），否则 manifest 永远误报 `torch_trace=false`。
2. **5 min 专验 torch_trace**（训练仍在时 dump）：
   ```bash
   ITERS=800 WARMUP=20 DUMP_WAIT_S=180 DUMP_PROBING_SQL=1
   PROBING_TORCH_PROFILING='on,rate=0'
   # 可选：PROBING_TORCH_MIN_STEP_INTERVAL=100  → 800 step 内应有 ≥8 锚点
   ```
   验收：`probing -t <pid> query "SELECT COUNT(*) FROM python.torch_trace"` ≥ steps/interval。
3. **trace_event 仍现**：pod 内 `probing -t <pid> query "SHOW probing.span_backends"` + 确认 wheel `backends.py` 默认与 `PROBING_SPAN_BACKENDS=none` 一致；查是否 `comm_collective` / `torch_step_timing` 连带注册。
4. **体积**：同 run 末 `du -sh /dev/shm/probing/<pid>` + `ls -lah` 各热环，对照 70 MiB 门槛。

---

## r3 fix2（2026-07-27）

见 **`PR1_DUMP_SMOKE_R3.md`**：`trace_event` **PASS**；`torch_trace` SQL **PASS**、`COUNT(*)=0`；`ITERS=800`+`DUMP_WAIT_S=180` 会 `attach=no`（训程短于等待）。

---

## 与 r1 对照

| 项 | r1 `182522` | r2 `190900` |
|----|-------------|-------------|
| `DUMP_WAIT_S` | 1200 | **240** |
| 门闩 waiting 行 | PASS | PASS |
| dump 执行 | 未知/中断 | **PASS** attach=ok |
| 训完 | 中断 | **3000 step** |
| §1.5 | 未验 | **PARTIAL** |

---

*验收人：Pillar C v3 PR-1 smoke 收尾 agent · 2026-07-27*
