# PR-2 实验 A5 · 定位验收（step_duration_sec 对 · pid 错配 FAIL）

> **run_id**：`20260728_101600-pillar-c-v3-pr2-localize-a5`  
> **判定**：**FAIL** · `culprit_rank=None` · `LOCALIZE_FALLBACK=1`

---

## 相对 A4

| 项 | A4 | A5 |
|----|----|-----|
| SQL 列 | `max(step_ms)` ❌ | **`max(step_duration_sec)`** ✅ |
| localize 墙钟 | 1.6s | **1651ms** ✅ |
| ATTACH_READY | 16/16 | **16/16 t=0** ✅ |
| 全 rank metric | attach OK · metric=None | attach=True · **ok=False · Traceback** |
| rank7 pid | — | dump **1180612** · localize **1185389** ❌ |
| culprit_rank | None | **None** |
| SET | fallback rank0 | **FALLBACK 48 pid 批量 SET** ❌ |

---

## 根因（A5→A6 修）

1. **列名已非主因**：`step_duration_sec` SQL 正确；手工对稳定 worker pid=1180612 查询返回 metric≈0.256。
2. **pid 选择错配**：同 `LOCAL_RANK=7` 存在多个 `/tmp/tbp_npu.py` 匹配 pid；localize 选了 **1185389**（无有效 probing 环 / Connection refused），dump 命中 **1180612**（有 `/dev/shm/probing/$pid/python.torch_trace`）。
3. **`_train_pids_by_rank` 旧逻辑**：按 attach ping + 较小 pid，无法区分「能 SHOW TABLES 但不能查 torch_step_timing」的僵尸/子进程。
4. **FALLBACK cands 过宽**：`CANDS_FALLBACK` 列出 **48** 个 pid（未做每 rank 去重 + shm 过滤）。

## 证据

```
SET_BEGIN … scope=localize victim=7
ATTACH_READY majority ok_n=16 t=0s
LOCALIZE_ELAPSED_MS=1651
CULPRIT_RANK=None CULPRIT_PID=None LOCALIZE_FALLBACK=1
LOCALIZE_FALLBACK_ALL_RANKS
CANDS_FALLBACK=1180605 … 1185468   # 48 pids
```

`localize.log` 首行：

```
mode=step_ms … culprit_rank=None fallback=True reason=sql_empty_or_timeout
query='SELECT COALESCE(max(step_duration_sec), 0) AS metric …'
```

rank7 行（截断）：`pid=1185389 local_rank=7 ok=False … raw_head='…Traceback…'`

---

## A6 修复方向

- 对齐 `dump_probing_sql.sh` `candidate_pids`：排除 launcher、无 `LOCAL_RANK`；**优先 `/dev/shm/probing/$pid/python.torch_trace`**；每 rank 一个 pid。
- `raw_head` 上限 **2000** 字符。
- hold_exec FALLBACK cands 走同一 `--list-worker-pids` 过滤。

---

## 路径

Pod：`…/pillar_c/20260728_101600-…/upgrade_rate_1.0/P3-SW-A/…/C2_probing/`  
本机 log：`pr2_localize/_prep/logs/launch_a5_20260728_101600.log`
