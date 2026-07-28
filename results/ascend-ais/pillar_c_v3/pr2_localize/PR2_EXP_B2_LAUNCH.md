# PR-2 实验 B2 · 发射记录（R5 · 活跃）

| 字段 | 值 |
|------|-----|
| **parent（活跃）** | `20260728_113719-pillar-c-v3-pr2-e3-b2` |
| arm | `20260728_113719-pillar-c-v3-pr2-e3-b2-upgrade_rate_1.0` |
| pod | `yysong-worker-0` |
| 废弃 | `111524` / `112615` / `112938` / `113422`（jsync 或 early exit 卡死） |
| B2 窗口 | `PILLAR_C_SET_WINDOW_STEPS=12` |
| jsync | `HOLD_EXEC_SKIP_HEAVY_JSYNC=1`（零 tar，bundle 已有脚本） |
| 全量臂 | REUSE v2 `20260725_230350-…/full_fidelity`（训完判分） |
| ETA | ~35–55 min（过 inject_stop=300） |

## 修复（相对 111524）
- 清跳板/本机残留 `kubectl exec`/`tar`/`ssh`
- `HOLD_EXEC_SKIP_HEAVY_JSYNC=1` 跳过 train/sidecar/dump/localize tar
- `jexec mkdir` / `FIRE` 加 `|| true` 防 `set -e` 早退

## 里程碑（~11:39 快照）
- ✅ warmup ok (20s)
- ✅ measure step 100
- ✅ SET_UPGRADE @ L=133 · pid=2027766 · rate=1.0 · LOCALIZE_FALLBACK=0
- ⚠️ SQL `culprit_rank=9`（GT=7；待终态核对 dense pid）
- ❌ SET_DOWNGRADE（窗降回未生效 · hold_exec 卡 jexec）
- ❌ hang@L137（11:38:58 stall → HANG_DETECTED 11:54:21 → BLOCKED）

发射：R5 @ 2026-07-28T11:37:19+08:00 · 终态 `PR2_EXP_B2_STATUS.md` · 判分 `PR2_E3_RATIO_B2.md`
