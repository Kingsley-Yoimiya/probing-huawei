# PR-2 实验 A2 · 定位验收（step_ms 修复后）

> **run_id**：`20260728_091413-pillar-c-v3-pr2-localize-a2`  
> **mode**：`PILLAR_C_LOCALIZE_MODE=step_ms`  
> **判定**：**FAIL**（`culprit_rank=None` → 全 rank FALLBACK SET）

---

## 相对 A1

| 项 | A1 | A2 |
|----|----|----|
| mode | `comm_max`（错） | **`step_ms`**（对） |
| SQL | `comm_collective` 全 0 | `torch_step_timing` 窗 [120,140] |
| culprit | rank **0**（tie-break） | **`None`**（attach 全失败） |
| SET 范围 | 仅 rank0 | **16 rank 全 SET_OK**（FALLBACK） |

---

## 验收

| # | 检查 | 结果 | 证据 |
|---|------|------|------|
| 1 | `localize.log` culprit_rank==7 | **FAIL** | `culprit_rank=None` · `fallback=True` · `reason=sql_empty_or_timeout` |
| 2 | 仅 rank7 SET_OK | **FAIL** | `LOCALIZE_FALLBACK=1` → **16×** `SET_OK_WORKER` |
| 3 | rank7 TT 升详 ≫ 其他 | **待定** | 全 rank rate=1.0 SET；dump 后另验 |

---

## 证据链

**`localize.log` 首行**：

```
LOCALIZE_SQL: ... mode=step_ms ... trigger_step=140 window=20 culprit_rank=None culprit_pid=None fallback=True reason=sql_empty_or_timeout
```

**per-rank**：全部 `ok=False` · `metric=None`（无一行 `ok=True`）

**`set_upgrade.log`**：

```
LOCALIZE_FALLBACK=1 culprit_rank=None culprit_pid=None
LOCALIZE_FALLBACK_ALL_RANKS
SET_OK_WORKER pid=358648 … pid=358663   # 16 workers
```

---

## 根因（A2）

1. **映射已修复**：`step_ms` SQL 正确；非 A1 的 comm_max 误映射。
2. **SET 瞬间 probing attach 全失败**：8a GC stall 导致 worker pid  churn；`probing -t $pid query` 全部 `ATTACH_FAIL` / timeout。
3. 编排层按设计走 **`LOCALIZE_FALLBACK_ALL_RANKS`** → 违背「仅 culprit SET」验收。

---

## 下一步（R3）

1. SET 前 **probing attach 就绪等待**（或 `PILLAR_C_LOCALIZE_TIMEOUT_S`↑ + 重试）
2. pid 稳定后再跑 localize（避开 8a stall 瞬时换 pid）
3. 可选：`step_ms` 空窗时 secondary 判据 `host_rss`（P3-SW-A 亦有 RSS 信号）

---

## A1 对照

见 `PR2_LOCALIZE_ACC.md`（A1：`comm_max` → rank0 FAIL）。
