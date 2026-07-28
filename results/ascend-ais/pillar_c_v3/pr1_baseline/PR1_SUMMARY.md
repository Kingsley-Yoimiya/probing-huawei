# PR-1 一页摘要

**日期**：2026-07-28  
**状态**：**PARTIAL** — 正式健康长 run §1.5 验收完成（**5 PASS + 1 估 PARTIAL**）

---

## 正式长 run（`20260727_210243-yjr-as-b-pr1-health`）

| # | §1.5 检查项 | 结果 |
|---|-------------|------|
| 1 | `cpu.utilization` 跨度 ≥1h | ✅ **~3600 s（1.0 h）**（DUMP_WAIT + 8MiB 环；query 尾部 100s） |
| 2 | `gpu.utilization` 跨度 ≥1h | ✅ **~3600 s（1.0 h）** |
| 3 | `torch_trace` 稀采 + worker pid | ✅ **n=7827**（≫48）；pid **2514880** rank7 |
| 4 | 无 `trace_event` / `variables` | ✅ SHOW TABLES 无 |
| 5 | 单 pid ≤70MB | ⚠️ **PARTIAL（估 ~33–41 MB）** — 无 dump du |
| 6 | `step_ms` 备注 | 📝 steady med **151.7 ms**；无 C0 对照 |

| 项 | 结果 |
|----|------|
| 16×24000 jsonl | ✅ |
| dump `attach=ok` @22:05:54 | ✅ |
| 本机备份 | ✅ `pr1_baseline/20260727_210243-yjr-as-b-pr1-health/` |

**详报**：`PR1_BASELINE.md`

---

## 前置 smoke（r5b · 已闭环）

| 项 | 结果 |
|----|------|
| 门闩 no-inject + attach=ok | ✅ |
| `python.trace_event` 不存在 | ✅ |
| `torch_trace` n=2549 @ worker rank7 | ✅ |
| pid-fix（排除 torchrun 父进程） | ✅ |

**详报**：`PR1_DUMP_SMOKE_R5.md`

---

## 关键路径

| 文档 | 路径 |
|------|------|
| **正式验收** | `pr1_baseline/PR1_BASELINE.md` |
| 发射记录 | `pr1_baseline/PR1_BASELINE_LAUNCH.md` |
| 代码交付 | `pr1_baseline/PR1_CODE_STATUS.md` |
| 正式 run 产物 | `pr1_baseline/20260727_210243-yjr-as-b-pr1-health/` |
| dump 脚本 | `project/probing-test/scripts/fail-slow/platform/ascend/dump_probing_sql.sh` |

---

## 遗留 / 下一步

1. **可选**：下轮 dump 增 `MIN(ts)/MAX(ts)` + `du` 探针，消掉 #5 PARTIAL。
2. **PR-2**：触发→定位→SET 时间线（见手册 §2）。
3. 同窗 C0 补 `step_ms` <1% 硬验（本 run 仅 NOTE）。
