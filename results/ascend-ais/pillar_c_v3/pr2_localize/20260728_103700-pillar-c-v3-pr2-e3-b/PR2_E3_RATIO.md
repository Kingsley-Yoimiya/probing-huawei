# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详

**状态：PARTIAL** — localize/SET **PASS**；**头条比 BLOCKED**（训程 hang@step122，不可与 v2 终态比）

> case=`P3-SW-A` · parent=`20260728_103700-pillar-c-v3-pr2-e3-b`  
> 动态臂复用：`20260728_102830-pillar-c-v3-pr2-localize-a6` · 全量臂：`reuse v2`  
> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。

## 结论（比率不可比）

| 尺 | 值 | 可用 |
|----|-----|------|
| 头条 W\* content est | **136.16%**（脚本算出） | **否** — hang 中途快照，16 rank 均有 sparse TT |
| raw 总落盘比 | **136.16%** | **否** — 同上 |
| v2 参考头条 | **72.6%** | 终态可比锚点 |
| `torch_trace_dense_ranks` | **16** | 目标 **1**（训完才成立） |

**BLOCKED 原因**：A6 `torchrun` 在 SET@step121 后 **hang@step122**（rank7 inductor 编译 / collective 等待）；训程已死、pod IDLE。动态臂 `probing_data` 为 hang 前快照：16 rank 各 183 行 sparse `rate=0` TT（仅 3 step），非 v2 终态「仅 culprit dense」。故 **136.16% > 100%** 为异常中途态，**不得**作为 PR-2 E3 验收头条。

## PR-2 验收（已完成部分）

| 项 | 值 | 判据 |
|----|-----|------|
| `culprit_rank` (SQL) | **7** | GT=7 ✅ |
| `culprit_pid` | **1459716** | 与 dump rank7 一致 ✅ |
| `LOCALIZE_FALLBACK` | **0** | 0 ✅ |
| SET | **SET_OK** ×1 pid=1459716 | SET_OK ✅ |
| dense pid == culprit pid | **Y** | Y ✅ |
| `torch_trace_dense_ranks` | **16** | == 1 ❌（hang 阻塞） |

### 语义翻转（已成立）

- **v2**：`hold_exec` 在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。
- **v3 PR-2**：`pillar_c_localize_culprit.py` SQL（`step_duration_sec` @ [101,121]）→ `PILLAR_C_SET_SCOPE=localize` → **仅 culprit pid 升详**。

- localize 首行：`LOCALIZE_SQL: … culprit_rank=7 culprit_pid=1459716 fallback=False reason=sql_max_metric`

## 分臂字节表（hang 快照，仅供审计）

| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |
|----|--------:|----:|-------:|:---:|:---:|------|
| 动态 | 2,439,978,128 | 2326.94 | 57,374,224 | Y | SET_OK | hang@122 中途态 |
| 全量 | 1,791,975,360 | 1708.96 | — | Y | n/a | reuse v2 上界 |
| 动态·W\*估 | 2,439,978,128 | 2326.94 | — | Y | SET_OK | W*=100；**不可比** |

### torch_trace 分 rank（hang 时全 rank sparse dense）

| pid | rows | steps | file_B | W* est_B |
|-----|-----:|------:|-------:|---------:|
| **1459716** (culprit) | 183 | 3 | 20,972,800 | 20,972,800 |
| 其余 15 rank | 183 | 3 | 20,972,800 | 20,972,800 |

## 判定：**PARTIAL**

- localize 语义：**PASS**
- E3 头条比：**BLOCKED**（hang@step122；需 B2 缓解后重测）
- JSON：`PR2_E3_RATIO.json`
- 详报：`PR2_EXP_B_STATUS.md`
- 本机：`results/ascend-ais/pillar_c_v3/pr2_localize/20260728_103700-pillar-c-v3-pr2-e3-b`
