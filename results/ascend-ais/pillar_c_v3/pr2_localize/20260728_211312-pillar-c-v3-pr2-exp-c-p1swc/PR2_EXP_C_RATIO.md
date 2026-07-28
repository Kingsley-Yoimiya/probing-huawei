# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详

> case=`P1-SW-C` · parent=`20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`
> 动态臂复用：`20260728_211312-pillar-c-v3-pr2-exp-c-p1swc` · 全量臂：`fresh`
> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。

## 结论：动态/全量 = **92.2%**（W*_content_est）

- v2 参考头条：**72.6%**（`pillar_c_v2/20260726_181423-…`）
- raw 总落盘比：`107.08%`
- W\* content est：`92.2%`
- 同覆盖（RSS 够归因）：**N**

## PR-2 验收

| 项 | 值 | 判据 |
|----|-----|------|
| `torch_trace_dense_ranks` | **16** | == 1 |
| `culprit_rank` (SQL) | **7** | GT=7 |
| dense pid == culprit pid | **Y** | Y |
| `LOCALIZE_FALLBACK` | **0** | 0 |
| SET | **SET_OK** | SET_OK |

### 语义翻转

- v2：脚本在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。
- v3 PR-2：`pillar_c_localize_culprit.py` SQL 定 culprit → `PILLAR_C_SET_SCOPE=localize` 仅 1 pid 升详。

- localize 首行：`LOCALIZE_SQL: query='SELECT COALESCE(avg(step_duration_sec), 0) AS metric FROM python.torch_step_timing WHERE local_step >= 18 AND local_step <= 118' mode=step_ms trigger_step=118 window=100 culprit_r`

## 分臂字节表

| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |
|----|--------:|----:|-------:|:---:|:---:|------|
| 动态 | 1872537584 | 1785.79 | 2907120 | N | SET_OK | orchestration_sql_localize_culprit_only_set |
| 全量 | 1748685056 | 1667.68 | — | Y | n/a | reuse_full_fidelity_upper_bound |
| 动态·W\*估 | 1612334016 | 1537.64 | — | N | SET_OK | W*=200 |

### torch_trace 分 rank（dense=行数>0）

| pid | rows | steps | file_B | W* est_B |
|-----|-----:|------:|-------:|---------:|
| 3564137 | 9647 | 107 | 20001280 | 3738557 |
| 3564138 | 9647 | 107 | 20001280 | 3738557 |
| 3564139 | 9647 | 107 | 20001280 | 3738557 |
| 3564140 | 9647 | 107 | 20001280 | 3738557 |
| 3564141 | 9647 | 107 | 20001280 | 3738557 |
| 3564142 | 9647 | 107 | 20001280 | 3738557 |
| 3564143 | 9647 | 107 | 20001280 | 3738557 |
| **3564144** | 9647 | 107 | 20001280 | 3738557 |
| 3564145 | 9647 | 107 | 20001280 | 3738557 |
| 3564146 | 9647 | 107 | 20001280 | 3738557 |
| 3564147 | 9647 | 107 | 20001280 | 3738557 |
| 3564148 | 9647 | 107 | 20001280 | 3738557 |
| 3564149 | 9647 | 107 | 20001280 | 3738557 |
| 3564150 | 9647 | 107 | 20001280 | 3738557 |
| 3564151 | 9647 | 107 | 20001280 | 3738557 |
| 3564152 | 9647 | 107 | 20001280 | 3738557 |

## 判定：**PARTIAL**

- JSON：`PR2_E3_RATIO.json`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`
