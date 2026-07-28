# PR-2 实验 B · 发射记录（数据量比重算）

**状态**：**PARTIAL** — localize **PASS**；E3 头条比 **BLOCKED**（hang@step122，见 `PR2_EXP_B_STATUS.md`）

---

## Run 标识

| 字段 | 值 |
|------|-----|
| parent | `20260728_103700-pillar-c-v3-pr2-e3-b` |
| 动态臂复用 | `20260728_102830-pillar-c-v3-pr2-localize-a6` / `upgrade_rate_1.0` |
| 全量臂 | 复用 v2 `pillar_c/20260725_230350-…/full_fidelity`（1.79GB） |
| pod | `yysong-worker-0` |
| 发射脚本 | `_prep/launch_exp_b.sh`（后台 poll IDLE ≤4h） |
| 判分 | `scripts/fail-slow/pr2_e3_score_ratio.py` |

## 方案

1. **动态臂**：不重跑；复用 A6（`e3a_upgrade` · `PILLAR_C_SET_SCOPE=localize` · `step_ms` SQL）
2. **全量臂**：`REUSE_FULL=1`（与 v2 E3 同锚点）
3. A6 训程结束后自动 pull + `PR2_E3_RATIO.md/json`

## 已确认（localize / SET）

| 项 | 值 |
|----|-----|
| `culprit_rank` | **7** |
| `culprit_pid` | **1459716** |
| `LOCALIZE_FALLBACK` | **0** |
| SET | **1× SET_OK_WORKER** |
| SQL | `step_duration_sec` @ window [101,121] |

## 收尾（2026-07-28 11:15）

- A6 训程 **hang@step122** 后已死；yysong-w0 **IDLE**
- localize/SET：**PASS**（culprit=7, pid=1459716）
- E3 比率：**BLOCKED** — hang 快照 dense=16，脚本算出 136.16% 不可与 v2 72.6% 比
- 详报：`PR2_EXP_B_STATUS.md` · 判分：`PR2_E3_RATIO.md`

## 下一：B2

- 短升详窗 + SET 后自动降回 rate=0，缓解单 rank rate=1.0 → collective hang

## 路径

- 后台 log：`pr2_localize/_prep/logs/launch_b_20260728_103700.log`
- A6 pod out：`…/pillar_c/20260728_102830-…/upgrade_rate_1.0/…/C2_probing/`
