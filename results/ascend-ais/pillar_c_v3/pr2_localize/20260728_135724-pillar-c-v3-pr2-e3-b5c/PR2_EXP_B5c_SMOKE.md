# PR-2 B5c 短测 · **PASS**

**日期**：2026-07-28 14:07  
**run_id**：`20260728_135724-pillar-c-v3-pr2-e3-b5c`  
**pod**：`yysong-worker-0` · rank=7 pid=4086564  
**outdir**：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_135724-pillar-c-v3-pr2-e3-b5c/upgrade_rate_1.0/P3-SW-A/by_pod/yysong-worker-0/round_1/C2_probing`

## 结果摘要

| 项 | 值 | 判据 |
|----|-----|------|
| **verdict** | **PASS** | __file__ OK + rows>0（主验收） |
| `probing.ext.torch.__file__` | `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/pydeps/probing/ext/torch.py` | pydeps 路径 ✅ |
| `_sync_live_tracers` in file | **True** | ✅ |
| SET rate=1.0 → culprit TT rows | **4368** | >0 ✅ |
| hot-updated log | **no** | 次要（日志可能不进 node_0） |
| rate=0 downgrade | no | 次要 |
| ITERS | 150 | 禁止 1800 |
| L SET/post | 30 → 123 | ≥20 step 后查 rows ✅ |
| 失败点 | hot-updated 日志缺失，**功能以 rows 为准** | |

## 验收链

1. jsync `ext/torch.py`（178 行，grep `_sync_live_tracers`=4）✅  
2. 训练常驻 `on,rate=0`；attach rank7 pid=4086564 ✅  
3. `__file__` 指向 pydeps；源文件含 `_sync_live_tracers` ✅  
4. `probing -t PID config probing.torch.profiling=on,rate=1.0` ✅  
5. post-SET `SELECT COUNT(*) FROM python.torch_trace` → **n=4368** ✅  

## torch_trace COUNT

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ n                                                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ 4368                                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 路径修复（134925 教训）

- `run_pillar_c_arm.sh` + `ARM=e3a_upgrade` → AFS 子目录 `upgrade_rate_1.0/`（非 `smoke/`）

## 下一步

- **B5c PASS** → **允许 B5d 全训（1800）**（父会话另派；本 tick 不开）
