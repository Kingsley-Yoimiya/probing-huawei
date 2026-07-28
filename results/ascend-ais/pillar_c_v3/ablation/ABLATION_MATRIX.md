# Pillar C v3 · 附录 A 离线删表消融矩阵

> **case**：P3-SW-A loud（host 泄漏 / `cpu.utilization` RSS 主证）  
> **日期**：2026-07-27  
> **执行**：本机离线；**不占集群**

## 数据与判分

| 项 | 路径 |
|---|---|
| full_fidelity dump（v2 锚点） | `project/probing-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity`（≈1.67 GiB / 1791975360 B AFS 记账） |
| 离线 D1–D3 jsonl | `project/probing-huawei/results/ascend-ais/20260725_012957-yjr-as-c-p3-sw-a-loud/P3-SW-A`（同配方 Loud formal，C1/C0=2.93） |
| 判分脚本 | `project/probing-test/scripts/fail-slow/score_dlevel_sql.py` |
| 消融工作区 | `project/probing-huawei/results/ascend-ais/pillar_c_v3/ablation/arm_*` |
| 复现 | `python3 …/ablation/run_ablation_matrix.py` |

**混合工作区说明**：`012957` 提供 C0/C1/C2 训练 jsonl（离线到 D3）；`full_fidelity` 提供完整 `probing_data` + C2 `query_manifest.json`（含 `python.torch_trace=true`）。删表 = 克隆 `probing_data` 后 `rm` 单表文件 + 更新 manifest / 相关 query 快照。

## 结果矩阵

| 删掉哪张表 | 表占体积（16 rank 合计） | 占 dump% | D-level | SQL 工具态 | 结论 |
|---|---:|---:|---|---|---|
| **无（对照）** | — | 100% | **D4** | PASS_D4 | 基线；`cpu.utilization_rss:max_kb=2777664` |
| `cpu.utilization` | 533 KiB（≈33 KiB/rank） | 0.03% | **D3** | TABLE_MISSING | **关键小表**；缺表即停 D4 |
| `gpu.utilization` | 533 KiB | 0.03% | **D4** | PASS_D4 | P3-SW-A 判分路径**不引用**此表（见下） |
| `cpu.tasks` | 523 KiB | 0.03% | **D4** | PASS_D4 | P3-SW-A 判分路径**不引用**此表 |
| `gpu.hccs` | 529 KiB | 0.03% | **D4** | PASS_D4 | manifest 未登记；删文件不影响 D4 |
| `python.torch_trace` | 305 MiB | 18.3% | **D4** | PASS_D4 | 对 host 泄漏是**噪音** |
| `python.trace_event` | 286 MiB | 17.1% | **D4** | PASS_D4 | 判分 0 引用 |
| `python.variables` | 305 MiB | 18.3% | **D4** | PASS_D4 | 判分 0 引用 |
| `python.torch_step_timing` | 305 MiB | 18.3% | **D4** | PASS_D4 | 步节奏辅助；P3-SW 不依赖 |
| `python.comm_collective` | 305 MiB | 18.3% | **D4** | PASS_D4 | 通信类；P3-SW 不用 |

**大表合计**：单删任一大表约 **−305 MiB（~18%）**；五张大表合计约 **~1.45 GiB（~87%）**，对照臂仍 D4。

## 与手册附录 A 预期对照

| 表 | 手册预期 D | 实测 D | 对齐？ |
|---|---|---|---|
| 对照 | D4 | D4 | ✅ |
| `cpu.utilization` | D1–D2 | **D3**（TABLE_MISSING） | ⚠️ 掉级方向一致，级数差 1（离线仍 D3，仅 SQL 不过） |
| `gpu.utilization` | D2–D3 | **D4** | ❌ `score_dlevel_sql` 的 P3-SW 分支只看 `cpu.utilization` |
| `cpu.tasks` | D3–D4 | **D4** | ❌ 同上 |
| 五张大表 | D4 | D4 | ✅ |

## 论文可用一句话

在 P3-SW-A 上，删掉五张 python 大表（合计约 **87%** 落盘体积）**不影响 D4** 归因；删掉 **0.03%** 体积的 `cpu.utilization` 小表则 **D4→D3**（`TABLE_MISSING`）。叙事：**省的是噪音、留的是关键**——关键表是周期 `cpu.utilization`（RSS），不是 torch 全量 trace。

## Evidence（逐臂）

### baseline

- `arm_baseline/scoring_table_SQL_loud.csv`：`d_level=D4`, `tool=PASS_D4`, `c1_c0=2.93`
- notes 片段：`cpu.utilization_rss:max_kb=2777664:p3sw_rss_window`

### drop_cpu.utilization

- manifest：`tables_present.cpu.utilization=false`
- query 快照已清空：`query_p3sw_rss_window.txt`, `query_cpu_util.txt`
- `tool_probing_sql=TABLE_MISSING` → **D3**

### drop_gpu.utilization / drop_cpu.tasks

- 文件已从 `probing_data/*/…` 删除；manifest 对应项置 false
- 判分仍 **D4**：`ext_evidence()` P3-SW 分支未检查这两张表（仅 `cpu.utilization` + RSS query）

### drop 五张大表

- 每臂 `probing_data` 减少约 320 MB；manifest / RSS query 不变 → **D4**

## 产物

- 本文件：`ABLATION_MATRIX.md`
- 机器可读：`ABLATION_MATRIX.json`
- 状态说明：`STATUS.md`
- _runner：`run_ablation_matrix.py`
