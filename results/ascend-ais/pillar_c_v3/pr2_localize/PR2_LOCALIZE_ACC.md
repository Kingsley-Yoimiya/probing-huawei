# PR-2 实验 A · 定位准确性验收

**run_id**：`20260728_084329-pillar-c-v3-pr2-localize-a`  
**arm**：`e3a_upgrade` · P3-SW-A loud · GT=**rank 7** · scope=`localize`  
**判定**：**FAIL**（定位错 rank；SET 打偏；非 FALLBACK 路径）

---

## 验收四项

| # | 检查项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | `localize.log`：`culprit_rank==7` | **FAIL** | `culprit_rank=0` · pid=53091 · `LOCALIZE_FALLBACK=0` |
| 2 | 仅 rank 7 SET；其余 15 rank 未升详 | **FAIL** | `set_upgrade.log`：`CANDS_LOCALIZE=53091`（local_rank=**0**）· 唯一 `SET_OK_WORKER` |
| 3 | dump 后 rank7 TT rows>0；其余稀采 | **INCONCLUSIVE** | dump 固定查 **victim pid=53098（rank7）** · `torch_trace COUNT=547`；但 SET 在 rank0，非「culprit 升详」语义 |
| 4 | FALLBACK_ALL_RANKS | **N/A** | 未触发（`LOCALIZE_FALLBACK=0`） |

---

## 根因

**SQL 模板与 case 机理不匹配**：

- P3-SW-A 为 **host_bound + inline_8a**（GC stall 注入 rank7），慢信号在 **host/cpu**，不在 NCCL。
- `pillar_c_localize_culprit.py` 对 P3-SW-A 映射 **`comm_max`** → 查 `python.comm_collective`。
- SET 触发时（L=138，窗 [118,138]）**16 rank 全部 metric=0.0** → tie-break 取 **rank 0**（非 GT 7）。
- 未走 `LOCALIZE_FALLBACK_ALL_RANKS`（有 SQL 结果，只是全零）。

`localize.log` 首行：

```
LOCALIZE_SQL: ... mode=comm_max ... culprit_rank=0 culprit_pid=53091 fallback=False reason=sql_max_metric
```

`set_upgrade.log` 摘要：

```
scope=localize victim=7
CULPRIT_RANK=0 CULPRIT_PID=53091
CANDS_LOCALIZE= 53091
SET_OK_WORKER pid=53091   # local_rank=0
```

---

## dump 旁证（victim rank7，08:48:48）

- `query_manifest.json`：`pid=53098` · `local_rank=7` · `attach=ok`
- `torch_trace COUNT(*)=547`（gmin=0, gmax=1002）— 来自 **常驻 rate=0** 稀采，**非** localize SET 升详
- `cpu.utilization` 窗内 rank7 rss ~2.9GB、cpu_total_pct 30–60%（8a 注入活跃）

---

## 路径

| 位置 | 路径 |
|------|------|
| AFS out | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_084329-pillar-c-v3-pr2-localize-a/upgrade_rate_1.0/.../C2_probing/` |
| 本机 | `pr2_localize/20260728_084329-pillar-c-v3-pr2-localize-a/`（localize.log · set_upgrade.log · probing/ 部分） |

---

## 建议复跑（实验 A′）

1. **改 mode**：`PILLAR_C_LOCALIZE_MODE=host_rss`（或修正 `CASE_MODE[P3-SW-A]` → `host_rss`）
2. 或 **tie-break**：metric 全零时 fallback 到 `SIDECAR_LOCAL_RANK`（victim/GT）
3. 发射勿经 `| head` 截断 pipe（本轮 hold_exec 本机监督提前断开；训程 orphan 至 ~2000 步自行结束）
