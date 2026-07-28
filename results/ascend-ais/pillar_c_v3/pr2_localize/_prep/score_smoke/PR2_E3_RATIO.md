# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详

> case=`P3-SW-A` · parent=`score_smoke`
> 动态臂复用：`smoke-v2` · 全量臂：`reuse v2`
> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。

## 结论：动态/全量 = **89.34%**（W*_content_est）

- v2 参考头条：**72.6%**（`pillar_c_v2/20260726_181423-…`）
- raw 总落盘比：`90.16%`
- W\* content est：`89.34%`
- 同覆盖（RSS 够归因）：**Y**

## PR-2 验收

| 项 | 值 | 判据 |
|----|-----|------|
| `torch_trace_dense_ranks` | **1** | == 1 |
| `culprit_rank` (SQL) | **None** | GT=7 |
| dense pid == culprit pid | **N** | Y |
| `LOCALIZE_FALLBACK` | **?** | 0 |
| SET | **SET_OK** | SET_OK |

### 语义翻转

- v2：脚本在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。
- v3 PR-2：`pillar_c_localize_culprit.py` SQL 定 culprit → `PILLAR_C_SET_SCOPE=localize` 仅 1 pid 升详。

- localize 首行：`—`

## 分臂字节表

| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |
|----|--------:|----:|-------:|:---:|:---:|------|
| 动态 | 1615633664 | 1540.79 | 13390080 | Y | SET_OK | orchestration_sql_localize_culprit_only_set |
| 全量 | 1791975360 | 1708.96 | — | Y | n/a | reuse_full_fidelity_upper_bound |
| 动态·W\*估 | 1601009072 | 1526.84 | — | Y | SET_OK | W*=100 |

### torch_trace 分 rank（dense=行数>0）

| pid | rows | steps | file_B | W* est_B |
|-----|-----:|------:|-------:|---------:|
| 1855451 | 54054 | 372 | 20001280 | 5376688 |
| 1855452 | 0 | 0 | 20001280 | 20001280 |
| 1855453 | 0 | 0 | 20001280 | 20001280 |
| 1855454 | 0 | 0 | 20001280 | 20001280 |
| 1855455 | 0 | 0 | 20001280 | 20001280 |
| 1855456 | 0 | 0 | 20001280 | 20001280 |
| 1855457 | 0 | 0 | 20001280 | 20001280 |
| 1855458 | 0 | 0 | 20001280 | 20001280 |
| 1855459 | 0 | 0 | 20001280 | 20001280 |
| 1855460 | 0 | 0 | 20001280 | 20001280 |
| 1855461 | 0 | 0 | 20001280 | 20001280 |
| 1855462 | 0 | 0 | 20001280 | 20001280 |
| 1855463 | 0 | 0 | 20001280 | 20001280 |
| 1855464 | 0 | 0 | 20001280 | 20001280 |
| 1855465 | 0 | 0 | 20001280 | 20001280 |
| 1855466 | 0 | 0 | 20001280 | 20001280 |

## 判定：**PARTIAL**

- JSON：`PR2_E3_RATIO.json`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/_prep/score_smoke`
