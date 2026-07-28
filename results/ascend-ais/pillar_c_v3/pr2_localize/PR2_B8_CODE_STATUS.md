# PR-2 B8 · Code 改动状态

**日期**：2026-07-28  
**改动范围**：python-only（Rust 不动，wheel 不重编）  
**部署 pod**：`grj-megatron-32card-0716-worker-0`（bundle=`/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`）

## 三处改动落地

### (a) `scripts/fail-slow/pillar_c_localize_culprit.py` — localize SQL 判据 & 窗口

- `SQL_TEMPLATES["step_ms"]` 默认聚合从 `max(step_duration_sec)` 改为 `avg(step_duration_sec)`（B7 mis-localize 的结构性原因：P3-SW-A `inline_8a` 让全 rank 同步 wait，max 抽奖挑瞬时最慢）
- 新增 `STEP_MS_AGG_EXPR` 映射：`avg`/`max`/`p95`（p95=`approx_percentile(step_duration_sec, 0.95)`）
- `build_sql` 在 `mode=step_ms` 时按 `PILLAR_C_LOCALIZE_STEP_AGG` 环境变量替换 SELECT 里的聚合表达式；保留旧 `max` 行为可回滚
- `main()` 新增 `PILLAR_C_LOCALIZE_STEP_WINDOW`（默认 100）**仅对 `step_ms` mode 生效**，覆盖旧 `PILLAR_C_LOCALIZE_WINDOW`（后者仍作用于 comm_max / host_rss）

自测（本机 python 3.9）：

```
== avg default ==
SELECT COALESCE(avg(step_duration_sec), 0) AS metric
FROM python.torch_step_timing
WHERE local_step >= 39 AND local_step <= 139

== max (B7 回滚) ==
SELECT COALESCE(max(step_duration_sec), 0) AS metric …

== p95 ==
SELECT COALESCE(approx_percentile(step_duration_sec, 0.95), 0) AS metric …

== comm_max (untouched) ==
SELECT COALESCE(max(duration_ms), 0) AS metric …
```

### (b) `scripts/fail-slow/hold_exec_run_case.sh` — no-jsonl-progress driver kill

- 在训练主 poll 循环（第 1120 行附近）加入兜底：每 10s 采一次 `stat -c "%Y %s" + wc -l` 汇总 `rank_*.jsonl` 的 mtime+size+行数签名
- 签名连续 `PILLAR_C_NO_PROGRESS_KILL_S` 秒不变 → append `NO_JSONL_PROGRESS_<S>S ts=<iso>` 到 `node_0.log`，`pkill -9 torchrun / tbp_npu / sidecar`，`touch node_0.done`
- 环境变量 `PILLAR_C_NO_PROGRESS_KILL_S`（默认 **90**；`0` 关闭）
- `kill_triggered` 变量避免二次触发；poll 每 60s 汇报 `no_progress_stall=Ns`

### (c) `scripts/fail-slow/hold_exec_run_case.sh` — HCCL_EXEC_TIMEOUT export

- torchrun 起进程前 `export HCCL_EXEC_TIMEOUT=${HCCL_EXEC_TIMEOUT:-600}`（默认 1800s=30min 太长；600s=10min 让 driver no-progress kill 或 stop_hang 有机会先跑）

## Gate 自检

`_prep/launch_exp_b8_smoke.sh` 起动时逐条 assert：

- `test_pillar_c_set_window.sh` → PASS
- localize.py `build_sql("step_ms", 139, 100)` 输出含 `avg(step_duration_sec)` + `local_step >= 39` + `local_step <= 139` → OK
- `hold_exec_run_case.sh` 含 `HCCL_EXEC_TIMEOUT` / `PILLAR_C_NO_PROGRESS_KILL_S` / `NO_JSONL_PROGRESS_` 三处关键字 → OK
- `bash -n hold_exec_run_case.sh` → OK

## Pod 部署确认

Pod bundle 已同步（jsync 走跳板 `ais-cf3e61a5`）：

- `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pillar_c_localize_culprit.py`（692 行）
- `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/_pillar_c_localize.py`（692 行，与上完全一致；per-run `${out}/_pillar_c_localize.py` 也从本地 jsync）
- `grep` 确认 pod 上文件已含 `PILLAR_C_LOCALIZE_STEP_AGG` / `PILLAR_C_LOCALIZE_STEP_WINDOW` / `approx_percentile` 关键行

`hold_exec_run_case.sh` 不需要 sync 到 pod（driver 端本机执行）。

## 回滚开关

- 复用 B7 行为：`PILLAR_C_LOCALIZE_STEP_AGG=max PILLAR_C_LOCALIZE_STEP_WINDOW=20`
- 关闭 no-progress kill：`PILLAR_C_NO_PROGRESS_KILL_S=0`
- 复用 HCCL 30min 默认：`HCCL_EXEC_TIMEOUT=1800`

## 相关 diff 文件

- `project/probing-huawei/scripts/fail-slow/pillar_c_localize_culprit.py`（`SQL_TEMPLATES`, `STEP_MS_AGG_EXPR`, `build_sql`, `main`）
- `project/probing-huawei/scripts/fail-slow/hold_exec_run_case.sh`（HCCL_EXEC_TIMEOUT export，poll loop no-progress kill 逻辑）
- `results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_b8_smoke.sh`（发射 + 三处 gate 自检）

## 实验 B8 状态（smoke）

见 `PR2_EXP_B8_SMOKE.md`。
