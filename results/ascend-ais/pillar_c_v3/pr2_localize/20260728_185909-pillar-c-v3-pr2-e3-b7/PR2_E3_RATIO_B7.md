# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详

> case=`P3-SW-A` · parent=`20260728_185909-pillar-c-v3-pr2-e3-b7`
> 动态臂复用：`20260728_185909-pillar-c-v3-pr2-e3-b7` · 全量臂：`reuse v2`
> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。

## 结论：动态/全量 = **47.67%**（raw_dump）

- v2 参考头条：**72.6%**（`pillar_c_v2/20260726_181423-…`）
- raw 总落盘比：`47.67%`
- W\* content est：`None%`
- 同覆盖（RSS 够归因）：**Y**

## PR-2 验收

| 项 | 值 | 判据 |
|----|-----|------|
| `torch_trace_dense_ranks` | **0** | == 1 |
| `culprit_rank` (SQL) | **5** | GT=7 |
| dense pid == culprit pid | **N** | Y |
| `LOCALIZE_FALLBACK` | **0** | 0 |
| SET | **SET_OK** | SET_OK |

### 语义翻转

- v2：脚本在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。
- v3 PR-2：`pillar_c_localize_culprit.py` SQL 定 culprit → `PILLAR_C_SET_SCOPE=localize` 仅 1 pid 升详。

- localize 首行：`LOCALIZE_SQL: query='SELECT COALESCE(max(step_duration_sec), 0) AS metric FROM python.torch_step_timing WHERE local_step >= 119 AND local_step <= 139' mode=step_ms trigger_step=139 window=20 culprit_r`

## 分臂字节表

| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |
|----|--------:|----:|-------:|:---:|:---:|------|
| 动态 | 854171456 | 814.60 | 48714496 | Y | SET_OK | orchestration_sql_localize_culprit_only_set |
| 全量 | 1791975360 | 1708.96 | — | Y | n/a | reuse_full_fidelity_upper_bound |

## 判定：**PARTIAL**

- JSON：`PR2_E3_RATIO.json`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260728_185909-pillar-c-v3-pr2-e3-b7`
