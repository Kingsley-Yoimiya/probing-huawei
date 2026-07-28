# PR-2 实验 A4 · 定位验收（并行有界 · 速度 PASS · 定位仍 FAIL）

> **run_id**：`20260728_095652-pillar-c-v3-pr2-localize-a4`  
> **判定**：**PARTIAL**（localize **1.6s** ✅ · `culprit_rank=None` ❌ · FALLBACK SET rank0）

---

## 相对 A3

| 项 | A3 | A4 |
|----|----|-----|
| localize 墙钟 | ~409s | **1646ms** ✅ |
| ATTACH_READY | 16/16 | **16/16** ✅ |
| step_ms 有 metric | 全 attach fail | attach OK 但 **metric=None 全 rank** |
| culprit_rank | None | **None** |
| SET | SET_FAIL_ALL | **1× SET_OK** pid=1052767（**非 rank7**，fallback 首 pid） |

---

## 证据

```
ATTACH_READY majority ok_n=16 t=0s
PILLAR_C_ATTACH_PREVALIDATED=1
LOCALIZE_ELAPSED_MS=1646
CULPRIT_RANK=None LOCALIZE_FALLBACK=1
LOCALIZE_FALLBACK_ALL_RANKS
SET_OK_WORKER pid=1052767   ← 仅 1 次，非 GT
```

`localize.log`：16 rank 并行 · 均有 `attach=True` · 均 `ok=False metric=None` · rank7 pid=1057353 同样空。

---

## 结论

- **R3/A4 代码目标（有界+快）达成**：不再拖过训程。
- **定位语义未过**：`step_ms` 窗 [110,130] 全 rank 无有效 metric；secondary `host_rss` 亦未产出 culprit（reason 仍为 sql_empty_or_timeout）。
- **下一刀**：查 `torch_step_timing` 在 8a stall 窗是否写入；或 victim-only 快路径 + `host_rss` 优先/扩窗。

---

## 路径

本机：`pr2_localize/pillar_c/20260728_095652-…/upgrade_rate_1.0/…/C2_probing/`
