# PR-2 实验 B · 判分收尾状态

**日期**：2026-07-28  
**总判**：**PARTIAL**（localize **PASS**；E3 头条比 **BLOCKED**）

---

## 1. 实验标识

| 字段 | 值 |
|------|-----|
| parent | `20260728_103700-pillar-c-v3-pr2-e3-b` |
| 动态臂 | 复用 A6 `20260728_102830-pillar-c-v3-pr2-localize-a6/upgrade_rate_1.0` |
| 全量臂 | 复用 v2 `pillar_c/20260725_230350-…/full_fidelity`（1.79 GB） |
| pod | `yysong-worker-0`（现 **IDLE**） |
| case | P3-SW-A |

## 2. localize 语义验收 — **PASS**

| 项 | 值 | 判据 |
|----|-----|------|
| `culprit_rank` (SQL) | **7** | GT=7 ✅ |
| `culprit_pid` | **1459716** | rank7 dump 一致 ✅ |
| `LOCALIZE_FALLBACK` | **0** | 0 ✅ |
| SET scope | `localize` | 仅 1 pid ✅ |
| SET | **SET_OK_WORKER** pid=1459716 | SET_OK ✅ |
| SQL | `step_duration_sec` @ [101,121] | `sql_max_metric` ✅ |

**语义翻转已成立**：v3 PR-2 用编排层 SQL 定 culprit，再 `PILLAR_C_SET_SCOPE=localize` 仅 culprit 升详；不再 v2 式 break 抢首个 ATTACH_OK worker。

## 3. 训程 hang — **BLOCKED 比率**

### 现象

- SET 在 **step 121** 触发（`SET_L=121`，`set_rate=1.0`，scope=localize）
- `rank_0007.jsonl` 在 **step 122** 停住（mtime ~10:28），此后无进展
- 疑因：culprit rank7 在 `rate=1.0` 下 inductor 编译 / host 堆积，拖住 **HCCL collective**，全 job stall
- 训程已死；yysong-w0 现 IDLE；**未重训**（用户约束）

### 数据快照（hang 中途，不可作终态比）

| 指标 | hang 快照 | v2 终态期望 |
|------|-----------|-------------|
| `torch_trace_dense_ranks` | **16** | **1** |
| 每 rank TT rows | 183（sparse rate=0） | culprit dense，其余 0 行 |
| 动态 total_B | 2.44 GB | ~1.6 GB（v2 rate_0 参考） |
| 脚本算出头条比 | **136.16%** | v2 参考 **72.6%** |

**结论**：136.16% 为 hang 前全 rank 仍在 baseline 采集的中途态，**>100%** 且 dense≠1，**不得**作为 PR-2 E3 验收头条。比率状态 = **BLOCKED**。

## 4. 本机数据

| 路径 | 大小 | 说明 |
|------|------|------|
| `…/20260728_103700-pillar-c-v3-pr2-e3-b/dynamic/` | ~2.3G | probing_data + case 日志 |
| `…/20260728_103700-pillar-c-v3-pr2-e3-b/full_fidelity/` | REUSE + total_dump_bytes | v2 上界锚点 |
| `…/pillar_c/20260728_102830-…/upgrade_rate_1.0/` | ~1.3G | A6 源臂 |
| 判分 | `PR2_E3_RATIO.md` / `.json` | 已更新 |

## 5. B2 建议（单 rank SET rate=1.0 → collective hang 缓解）

根因假设：仅 culprit 升详时，该 rank 单步耗时暴增（torch_trace + inductor），其余 rank 在 collective 屏障等待 → 全局 hang。

| 方案 | 做法 | 预期 |
|------|------|------|
| **B2-a 短升详窗** | SET rate=1.0 仅持续 N step（如 5～10），定时器自动 SET 回 rate=0 | 采集够 W\* 窗口后恢复同步；训程可跑完 |
| **B2-b 升详后降回** | step 121 SET 1.0 → step 125 SET 0.0（culprit 仍 localize scope） | 与 v2 E4 互补：省量需配升详，但升详不能常驻 |
| **B2-c 异步屏障** | 升详窗内 culprit 跳过或弱化参与 blocking collective（需框架配合） | 根治 stall，改动面大 |
| **B2-d 分级 rate** | 先 rate=0.5 试探 2 step，无 stall 再 1.0 | 降低 inductor 突发 |
| **B2-e 编译预热** | SET 前对 culprit 预跑 dummy forward（或 `torch.compile` warmup） | 把编译摊到 SET 前 |

**推荐下一轮**：**B2-a + B2-b**（短窗升详 + 自动降回），复用 A6 localize 路径，仅改 `hold_exec` SET 时序；目标 `dense_ranks=1` 且训程跑过 inject_stop=300。

## 6. 产物索引

- 比率：`PR2_E3_RATIO.md` / `PR2_E3_RATIO.json`
- 发射：`PR2_EXP_B_LAUNCH.md`
- 代码状态：`PR2_CODE_STATUS.md`
- hang 证据：`_prep/a6_hang_evidence_20260728_1045/`
