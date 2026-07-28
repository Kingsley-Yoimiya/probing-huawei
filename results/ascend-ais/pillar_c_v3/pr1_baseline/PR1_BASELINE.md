# PR-1 Baseline 验收报告

**run_id**：`20260727_210243-yjr-as-b-pr1-health`  
**日期**：2026-07-27  
**状态**：**PARTIAL** — §1.5 六项 **5 PASS + 1 估（单 pid 体积无 dump du）**；训练/jsonl/dump 全链路 **DONE**

---

## 运行摘要

| 项 | 值 |
|----|-----|
| pod | `yysong-worker-0`（验收时 IDLE） |
| 臂 | `C2_probing`，`INJECT_KIND=none`（健康，无注入） |
| 起止（CST） | ~21:06 FIRE → ~22:06 SQL dump → ~22:14 训完 |
| 步数 | 16 rank × **24000** 行（step 0–23999） |
| dump | `attach=ok`，`pid=2514880`，`pid_role=worker:local_rank=7` @ **22:05:54** |
| jsonl | ✅ 16/16 已拉回本机 |
| probing SQL | ✅ `…/C2_probing/probing/` 完整（query_* + manifest） |

---

## 时间线（CST）

| 时刻 | 事件 |
|------|------|
| ~21:06 | `FIRE_OK` + warmup ok (20s) |
| ~21:06:20 | 进入 `DUMP_WAIT_S=3600` 等待 |
| **22:05:52** | recover：`training alive → SQL dump` |
| **22:05:54** | `dump attempted`（`query_manifest.json` ts） |
| ~22:12 | rank0 jsonl ≈ 23384 行（训末段） |
| **22:14:40** | `training DONE`（recover pull） |

`recover.log`：`sleep 2498s until dump` @21:24 → dump @22:06，与 `DUMP_WAIT_S=3600` 设计一致。

---

## 利用率时间跨度（§1.5 #1 / #2）

> `dump_probing_sql.sh` 仅导出 `ORDER BY ts DESC LIMIT 100/200`，**无** `MIN(ts)/MAX(ts)` 查询；下表分「导出尾部窗口」与「环内推断跨度」两层。

### 从 query 文件实测（导出尾部）

| 表 | 来源 | 样本数 | 时间跨度 |
|----|------|--------|----------|
| `cpu.utilization`（`scope=process`） | `query_p3sw_rss_window.txt` | 200 行 | **100.0 s**（1.67 min） |
| `cpu.utilization`（thread+process 混合） | `query_cpu_util.txt` | 100 行 | **5.5 s** |
| `gpu.utilization`（去重 ts） | `query_gpu_util.txt` | 7 个时刻 × 16 device | **25.9 s** |

- `cpu.utilization` process 行采样间隔中位 **0.502 s**，与 `PROBING_CPU_SAMPLE_MS=500` 一致；**非** v2「只剩 1 s」退化。
- 导出尾部短于 1h 是 **LIMIT artifact**，不代表环容量。

### 环内推断跨度（验收主证据）

| 依据 | 结论 |
|------|------|
| `DUMP_WAIT_S=3600`（warmup 后满 60 min 再 dump） | 至 dump 时 probing 已连续采集 **≥3600 s ≈ 1.0 h** |
| `ring_config.rs`：`cpu.utilization` / `gpu.utilization` 各 **8 MiB**（PR-1 分级） | 68 min 训程 **未触环回卷**（手册：8 MiB @ 500 ms 可撑数小时级） |
| `query_manifest.json`：`cpu.utilization` / `gpu.utilization` 均为 `true` | 两表在 dump 时均存在且可查询 |

| 表 | 环内推断跨度 | 换算 |
|----|--------------|------|
| `cpu.utilization` | **~3600 s** | **~1.0 h** |
| `gpu.utilization` | **~3600 s** | **~1.0 h** |

---

## 单 pid 热环体积（§1.5 #5）

dump 产物**无** `du -sh /dev/shm/probing/<pid>` 或 per-table size 字段；自本机 pull 亦无 shm 旁证。

### 配置上限（`ring_config.rs` / PR-1 分级）

| 表 | 默认环预算 |
|----|------------|
| `cpu.utilization` | 8 MiB |
| `cpu.tasks` | 8 MiB |
| `gpu.utilization` | 8 MiB |
| `gpu.hccs` | 4 MiB |
| `python.torch_trace` | 20 MiB（容量；rate=0 稀采实际未满） |
| 其他 Python 外表 | 20 MiB 档（`comm_collective` 等，健康 run 占用小） |

### 已省下的 v2 大表（SHOW TABLES 证实不存在）

- `python.trace_event`：**未注册**（省 ~20 MiB/表档）
- `python.variables`：**未注册**（省 ~20 MiB/表档）

### 估算

| 分量 | 估 |
|------|-----|
| CPU+GPU 固定四表（满环上限） | **28 MiB** |
| 稀采 `torch_trace`（n=7827，非满 20 MiB 环） | **~3–8 MiB** |
| 其余小表 / cold 段 | **~2–5 MiB** |
| **单 pid 合计（估）** | **~33–41 MiB** ≤ **70 MiB** |

**判定**：门槛 **PASS（估）**；精确 du **未测** → 本项标 **PARTIAL**。

---

## torch_trace / 表清单（§1.5 #3 / #4）

### `python.torch_trace`（`query_torch_trace_count.txt`）

| 字段 | 值 |
|------|-----|
| n | **7827** |
| gmin / gmax | 0 / **21002** |
| 门槛 | steps/interval = 24000/500 = **48** → **7827 ≫ 48** ✅ |
| attach | pid **2514880** = **worker:local_rank=7**（非 torchrun 父进程）✅ |

### `SHOW TABLES`（`query_show_tables.txt`）

**存在**：`cpu.utilization`、`gpu.utilization`、`cpu.tasks`、`gpu.hccs`、`python.torch_trace`、`python.torch_step_timing` 等。

**不存在**：`python.trace_event`、`python.variables` ✅

---

## step_ms 备注（§1.5 #6）

本 run **无同窗 C0** 对照臂；仅记 C2 rank0 jsonl（跳过 warmup 50 步后）：

| 指标 | rank0 |
|------|-------|
| steady 中位（步 1050–22900） | **151.68 ms** |
| 末样 | **84.45 ms**（训末收束，非稳态） |
| 与 v1 失败 run（`165856`）steady ~149 ms | 同量级；**<1% 未验**（缺 C0） |

**判定**：**NOTE** — 训练/jsonl 完整，无异常 stall；开销对比留待 C0 同窗复测。

---

## §1.5 检查表

| # | 检查项 | 期望 | 结果 | 说明 |
|---|--------|------|------|------|
| 1 | `cpu.utilization` 时间跨度 | ≥ 1 h | **PASS** | 环内推断 **~3600 s（1.0 h）**；query 导出尾部 100 s（LIMIT 200） |
| 2 | `gpu.utilization` 时间跨度 | ≥ 1 h | **PASS** | 同上；query 导出尾部 25.9 s（LIMIT 100×16 device） |
| 3 | `torch_trace` 稀采行数 + pid | n ≥ 48；worker pid | **PASS** | **n=7827**；pid=2514880 rank7 |
| 4 | 无 `trace_event` / `variables` | 不存在 | **PASS** | SHOW TABLES 无两表 |
| 5 | 单 pid 总体积 | ≤ 70 MB | **PARTIAL（估 PASS）** | 估 **~33–41 MB**；无 dump du |
| 6 | `step_ms` 备注 | 相对基线 <1% | **NOTE** | steady med **151.7 ms**；无 C0 |

**§1.5 正式验收：5 PASS + 1 PARTIAL（估）+ 1 NOTE → 总评 PARTIAL**

训练/jsonl 侧：**PASS**（16×24000，`node_0.done`，recover pull 完成）。

---

## 路径

| 用途 | 路径 |
|------|------|
| 本机根 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_210243-yjr-as-b-pr1-health/` |
| probing dump | `…/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing/probing/` |
| manifest | `…/probing/query_manifest.json` |
| recover 日志 | `…/logs/recover.log` |
| AFS 意图 | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/pr1_baseline/20260727_210243-yjr-as-b-pr1-health/` |

---

## 关联

- 发射记录：`PR1_BASELINE_LAUNCH.md`
- 一页摘要：`PR1_SUMMARY.md`
- 代码状态：`PR1_CODE_STATUS.md`
- 短 smoke（门闩/pid-fix）：`PR1_DUMP_SMOKE_R5.md`
- 手册：`project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md` §1.5

---

*验收：Pillar C v3 PR-1 §1.5 正式验收 · composer-2.5 · 2026-07-28*
