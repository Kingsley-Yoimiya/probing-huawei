# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详

> case=`P3-SW-A` · parent=`20260728_132612-pillar-c-v3-pr2-e3-b5b`
> 动态臂复用：`20260728_132612-pillar-c-v3-pr2-e3-b5b` · 全量臂：`reuse v2`
> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。

## 结论：动态/全量 = **116.72%**（W*_content_est）

- v2 参考头条：**72.6%**（`pillar_c_v2/20260726_181423-…`）
- raw 总落盘比：`135.45%`
- W\* content est：`116.72%`
- 同覆盖（RSS 够归因）：**N**

## PR-2 验收

| 项 | 值 | 判据 |
|----|-----|------|
| `torch_trace_dense_ranks` | **0** | == 1 |
| `culprit_rank` (SQL) | **7** | GT=7 |
| dense pid == culprit pid | **N** | Y |
| `LOCALIZE_FALLBACK` | **0** | 0 |
| SET | **SET_OK** | SET_OK |

### 语义翻转

- v2：脚本在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。
- v3 PR-2：`pillar_c_localize_culprit.py` SQL 定 culprit → `PILLAR_C_SET_SCOPE=localize` 仅 1 pid 升详。

- localize 首行：`LOCALIZE_SQL: query='SELECT COALESCE(max(step_duration_sec), 0) AS metric FROM python.torch_step_timing WHERE local_step >= 110 AND local_step <= 130' mode=step_ms trigger_step=130 window=20 culprit_r`

## 分臂字节表

| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |
|----|--------:|----:|-------:|:---:|:---:|------|
| 动态 | 2427145868 | 2314.71 | 44539088 | N | SET_OK | orchestration_sql_localize_culprit_only_set |
| 全量 | 1791975360 | 1708.96 | — | Y | n/a | reuse_full_fidelity_upper_bound |
| 动态·W\*估 | 2091581068 | 1994.69 | — | N | SET_OK | W*=100 |

### torch_trace 分 rank（dense=行数>0）

| pid | rows | steps | file_B | W* est_B |
|-----|-----:|------:|-------:|---------:|
| 3602537 | 0 | 0 | 20972800 | 0 |
| 3602538 | 0 | 0 | 20972800 | 0 |
| 3602539 | 0 | 0 | 20972800 | 0 |
| 3602540 | 0 | 0 | 20972800 | 0 |
| 3602541 | 0 | 0 | 20972800 | 0 |
| 3602542 | 0 | 0 | 20972800 | 0 |
| 3602543 | 0 | 0 | 20972800 | 0 |
| **3602544** | 0 | 0 | 20972800 | 0 |
| 3602545 | 0 | 0 | 20972800 | 0 |
| 3602546 | 0 | 0 | 20972800 | 0 |
| 3602547 | 0 | 0 | 20972800 | 0 |
| 3602548 | 0 | 0 | 20972800 | 0 |
| 3602549 | 0 | 0 | 20972800 | 0 |
| 3602550 | 0 | 0 | 20972800 | 0 |
| 3602551 | 0 | 0 | 20972800 | 0 |
| 3602556 | 0 | 0 | 20972800 | 0 |

## 判定：**PARTIAL**

- JSON：`PR2_E3_RATIO.json`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260728_132612-pillar-c-v3-pr2-e3-b5b`
