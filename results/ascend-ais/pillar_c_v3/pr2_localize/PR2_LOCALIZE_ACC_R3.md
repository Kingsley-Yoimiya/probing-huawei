# PR-2 实验 A3 · 定位验收（attach-wait 有效，localize 超时挂起）

> **run_id**：`20260728_093112-pillar-c-v3-pr2-localize-a3`  
> **mode**：`PILLAR_C_LOCALIZE_MODE=step_ms`  
> **判定**：**FAIL**（`culprit_rank=None` · localize 串行超时 · SET_FAIL_ALL）

---

## 相对 A2

| 项 | A2 | A3 |
|----|----|----|
| attach 预检 | 无 | **ATTACH_READY majority ok_n=16 t=0s** ✅ |
| localize 耗时 | ~instant 全 fail | **~409s**（09:32:56→09:39:45）串行 16×attach-wait |
| localize 结果 | 全 attach fail | 10 rank 行 attach=False（仅部分写入后 budget 耗尽） |
| SET | 16× FALLBACK SET_OK | **CANDS_FALLBACK 空** → SET_FAIL_ALL |
| 训程 | 进行中 SET | jsonl=**2000** 已跑完，SET 阶段训程已结束 |

---

## 验收

| # | 检查 | 结果 | 证据 |
|---|------|------|------|
| 1 | ATTACH_READY（R3 新增） | **PASS** | `set_upgrade.log` L6：`ok_n=16 t=0s` |
| 2 | `localize.log` culprit_rank==7 | **FAIL** | `culprit_rank=None` · `reason=sql_empty_or_timeout` |
| 3 | 仅 rank7 SET_OK | **FAIL** | `SET_FAIL_ALL` · 无 SET_OK 行 |
| 4 | 非 FALLBACK | **FAIL** | `LOCALIZE_FALLBACK=1` → fallback 但 cands 空 |

---

## 证据链

**`set_upgrade.log`**（522B）：

```
SET_BEGIN … SET_L=128
ATTACH_CFG wait_s=45 probe_timeout_s=25 …
ATTACH_READY majority ok_n=16 t=0s lr= 0..15
CULPRIT_RANK=None LOCALIZE_FALLBACK=1
LOCALIZE_FALLBACK_ALL_RANKS
CANDS_FALLBACK=          ← 训程结束后 worker 已死
SET_FAIL_ALL
SET_END ts=2026-07-28T09:39:45+08:00   ← localize 块 ~7min
```

**`localize.log`**（1064B，非空但 incomplete）：

```
LOCALIZE_SQL: … mode=step_ms … culprit_rank=None fallback=True reason=sql_empty_or_timeout
LOCALIZE_RANK pid=… local_rank=0..9 ok=False attach=False   ← 仅 10 行后停止
```

**训程**：`rank_0000.jsonl` = **2000** 行 · pod 现无 torchrun。

---

## 根因（A3）

1. **ATTACH_READY 有效**：shell 预检 16 rank 均可 `probing query SHOW TABLES`；R3 attach-wait **PASS**。
2. **localize 仍串行长等待**：`pillar_c_localize_culprit.py` 对每个 pid 独立 `_probe_attach_ready(wait=45s)` × retries × secondary，总墙钟 **>7min**。
3. **训程窗口错过**：SET 触发时 L=128，localize 挂起期间训程跑完；fallback 重扫 pid → **空 cands**。
4. **091413（A2）**：本机 hold_exec 已于 ~09:25 结束；**094730** 复跑（旧代码）在 tick#4 已 kill。

---

## R3→A4 修复

| 改动 | 说明 |
|------|------|
| **并行查询** | `ThreadPoolExecutor` 16 worker 同时 SQL |
| **有界总预算** | `PILLAR_C_LOCALIZE_TOTAL_BUDGET_S` 默认 60s（prevalidated） |
| **跳过重复 attach** | `PILLAR_C_ATTACH_PREVALIDATED=1` → per-pid 直接 query，timeout=8s |
| **SET 块硬超时** | `timeout ${PILLAR_C_SET_BLOCK_TIMEOUT_S:-120}` 包住 localize |

---

## 对照

- A1：`PR2_LOCALIZE_ACC.md`（comm_max → rank0）
- A2：`PR2_LOCALIZE_ACC_R2.md`（无 attach-wait → 全 fail FALLBACK SET）
