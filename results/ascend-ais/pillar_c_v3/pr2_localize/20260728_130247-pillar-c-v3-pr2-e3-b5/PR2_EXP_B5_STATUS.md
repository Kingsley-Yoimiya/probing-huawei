# PR-2 实验 B5 · PARTIAL

**日期**：2026-07-28  
**parent**：`20260728_130247-pillar-c-v3-pr2-e3-b5`  
**verdict**：**PARTIAL**

| 项 | 值 | 门槛 | 判定 |
|----|-----|------|------|
| SET_DOWNGRADE_OK | ✅ 原生 `reason=time` | ≥1 | PASS |
| localize culprit | ✅ **7** | 7 | PASS |
| inject_stop | ✅ step 300 marker | ≥1 | PASS |
| 非 culprit TT `n_rows` | ✅ **0**（16/16） | ≈0 | PASS |
| culprit TT `n_rows` | ❌ **0** | ≫0 | **FAIL** |
| `torch_trace_dense_ranks` | ❌ **0** | 1 | FAIL |
| headline W* | ❌ **114.99%** | <100% | FAIL |
| headline raw | 133.72%（未改 total 口径） | — | — |

## AFS 核实（pod 内 MEMT 解析）

```
culprit 3401343/python.torch_trace: n_rows=0, file_bytes=20972800, rows_overwritten=0
全部 16 rank: nonzero ranks = 0
culprit python.torch_step_timing: n_rows=1848（tracer 存活，仅 wall 计时）
```

**非拉盘截断**：本地与 AFS 一致；`torch_trace` mtime 13:03（SET 前预分配环），SET 窗 step **134→249**（~115 步 @ rate=1.0）应写入但未落行。

## 根因（一句）

**B5 只 jsync 了 `torch_probe.py`（rate=0 零行 ✅），bundle 内 `ext/torch.py` 仍是 Jul-27 旧版（116 行、无 C0 热更块）→ SET `probing.torch.profiling=on,rate=1.0` 只改 config，optimizer hook 首步后不再读 spec，live tracer 永久 rate=0。**

| 组件 | bundle 状态 | 影响 |
|------|-----------|------|
| `torch_probe.py` | ✅ B5 版（rate=0 零行） | 16 rank 空环正确 |
| `ext/torch.py` | ❌ **无** `_last_spec` / `_sync_live_tracers` / 热更 | SET rate=1.0 **无效** |

`rate=0` 默认零行**未误伤** rate=1.0 采样逻辑；问题是 SET 根本未推到 tracer。

## 评分口径

| 尺 | 行为 | v2 对齐 |
|----|------|---------|
| **raw** `total_dump_bytes` | 含 16×20MB 空环文件大小 | v2 同（320MB TT 壳） |
| **W\* headline** | `n_rows=0` → `est_tt_bytes_w=0`（已修 `pr2_e3_score_ratio.py`） | v2 空环内容按 0 |
| 115% 仍偏高 | culprit 无详采 + B5 周期表远大于 v2（`cpu.utilization` 285MB vs v2 0.5MB） | 待 B5r2 culprit 写满后再比 |

## 下一步：B5r2（单跑，不重改 torch_probe）

1. jsync **`ext/torch.py`**（本地 179 行 C0+直写 tracer 版）到 `probe-bundle/pydeps`
2. 其余同 B4/B5（localize + culprit SET + 30s 降回）
3. 预期：dense=1、culprit TT rows≫0、headline 近 v2 72.6%

- 诊断：`PR2_EXP_B5_DIAG.md`
- 判分：`PR2_E3_RATIO_B5.md` / `.json`
