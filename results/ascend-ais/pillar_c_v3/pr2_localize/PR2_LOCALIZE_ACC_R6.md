# PR-2 实验 A6 · 定位验收（pid 对齐 dump · PASS）

> **run_id**：`20260728_102830-pillar-c-v3-pr2-localize-a6`  
> **判定**：**PASS** · `culprit_rank=7` · `LOCALIZE_FALLBACK=0`

---

## 相对 A5

| 项 | A5 | **A6** |
|----|----|--------|
| pid 选择 | rank7=1185389（错） | **1459716（与 dump 一致）** ✅ |
| 全 rank query | ok=False Traceback | **16/16 ok=True** ✅ |
| culprit_rank | None | **7** ✅ |
| LOCALIZE_FALLBACK | 1 | **0** ✅ |
| SET | 48 pid 批量 | **1× SET_OK pid=1459716** ✅ |
| localize 墙钟 | 1651ms | 8722ms（含 per-rank shm 评分） |

---

## 证据

**localize.log** 首行：

```
mode=step_ms … culprit_rank=7 culprit_pid=1459716 fallback=False reason=sql_max_metric
query='SELECT COALESCE(max(step_duration_sec), 0) AS metric … local_step >= 101 AND local_step <= 121'
```

**set_upgrade.log**：

```
ATTACH_READY majority ok_n=16 t=0s
LOCALIZE_ELAPSED_MS=8722
CULPRIT_RANK=7 CULPRIT_PID=1459716 LOCALIZE_FALLBACK=0
CANDS_LOCALIZE= 1459716
SET_OK_WORKER pid=1459716
```

**dump.log**（rank7 对齐）：

```
dump_probing_sql … pid=1459716 attach=ok pid_role=worker:local_rank=7
```

rank7 metric=0.261；全局 max 在 rank3=0.263，**victim 2% tie-break** 保留 GT rank7。

---

## 修复摘要

1. `worker_pids_by_rank` 对齐 `dump_probing_sql.sh`：shm `python.torch_trace` 评分 + 每 rank 单 pid。
2. `raw_head` 上限 2000。
3. hold_exec FALLBACK/`victim`/`all` cands 走 `--list-worker-pids`。
4. pytest **13/13** PASS。

---

## 路径

Pod：`…/pillar_c/20260728_102830-…/upgrade_rate_1.0/P3-SW-A/…/C2_probing/`  
本机 log：`pr2_localize/_prep/logs/launch_a6_20260728_102830.log`
