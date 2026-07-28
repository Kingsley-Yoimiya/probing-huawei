# PR-2 代码状态 · 触发→定位→SET

> **日期**：2026-07-28  
> **状态**：**B5c PASS → 允许 B5d 全训（B5b hang@134 已闭环于短测）**

## 改动清单（A5→A6）

| 编号 | 文件 | 改动 |
|------|------|------|
| **2.1.a-a6** | `pillar_c_localize_culprit.py` | `candidate_worker_pids` + shm 评分；`--list-worker-pids`；`RAW_HEAD_MAX=2000` |
| **2.1.a-a6** | `hold_exec_run_case.sh` | FALLBACK/victim/all cands 走 localize 过滤 |
| 2.1.a-a5 | `pillar_c_localize_culprit.py` | `step_duration_sec`；victim 2% tie-break |
| 2.1.a-a4 | 同上 | 16 路并行 · total_budget · prevalidated |
| 测试 | `test_pillar_c_localize_culprit.py` | **13 passed** |

## 实验 A 状态

| 轮次 | run_id | 结果 | culprit_rank |
|------|--------|------|--------------|
| A1 | `20260728_084329…` | FAIL（comm_max） | 0 |
| A2 | `20260728_091413…` | FAIL（无 attach-wait） | None |
| A3 | `20260728_093112…` | FAIL（串行挂起） | None |
| A4 | `20260728_095652…` | PARTIAL（step_ms 列错） | None |
| A5 | `20260728_101600…` | **FAIL**（pid 错配） | **None** |
| **A6** | `20260728_102830…` | **PASS** | **7** ✅ |

## Gate

- pytest 13/13 PASS
- A5 验收：`PR2_LOCALIZE_ACC_R5.md`
- A6 验收：`PR2_LOCALIZE_ACC_R6.md` · 发射：`PR2_EXP_A6_LAUNCH.md`
- dump rank7 pid **1459716** = localize culprit = SET_OK

## 实验 B 状态

| 轮次 | run_id | localize | E3 头条比 | 备注 |
|------|--------|----------|-----------|------|
| **B** | `20260728_103700-pillar-c-v3-pr2-e3-b` | **PASS**（culprit=7） | **BLOCKED** | hang@step122；dense=16；脚本快照 136.16% 不可比 → 见 `PR2_EXP_B_STATUS.md` |
| **B2 R5** | `20260728_113719-pillar-c-v3-pr2-e3-b2` | culprit=**9**≠GT7 | **BLOCKED** | hang@L137；dense=0；SET_DG=0；窗降回未生效（hold_exec 卡 jexec） |

## 实验 B3 状态（时基降回）

| 编号 | 文件 | 改动 |
|------|------|------|
| **B3** | `hold_exec_run_case.sh` | `PILLAR_C_SET_WINDOW_S` 时基优先；`read_set_upgrade_field` 重试；victim=7 fallback；`jexec_poll` python timeout；降回阻塞 `jexec` |
| **B3** | `test_pillar_c_set_window.sh` | 时基 + 禁止 skip 自检 |

| 轮次 | run_id | localize | E3 头条比 | 备注 |
|------|--------|----------|-----------|------|
| R1 | `20260728_115719…` | culprit=12≠7 | — | 作废：SET_DG skip |
| **R3** | `20260728_122300…` | **7** ✅ | **133.72%** PARTIAL | SET_DG reason=time；dense=16；训完 1800 行 |
| R4 | `123100` | — | — | **已 kill**（冲突 R3） |

## 实验 B4 状态（pod 原生降回）

| 轮次 | run_id | localize | E3 头条比 | 备注 |
|------|--------|----------|-----------|------|
| **B4** | `20260728_124450…` | culprit=**7** ✅ | **133.72%** PARTIAL | SET_DG **原生** reason=time；dense=16；jexec_poll python OK |

## 实验 B5 状态（rate=0 零行 + 热更修复）

| 文件 | 改动 |
|------|------|
| `torch_probe.py` | rate=0 默认不采样；稀采 opt-in `PROBING_TORCH_SPARSE_ANCHOR=1` |
| `ext/torch.py` | SET 热更直写 live tracer（修 NPU gc sweep 漏同步） |
| `pr2_e3_score_ratio.py` | 空环 `n_rows=0` → W\* est=0 |

| 轮次 | run_id | localize | E3 头条比 | dense | 备注 |
|------|--------|----------|-----------|-------|------|
| **B5** | `20260728_130247…` | culprit=**7** ✅ | **114.99%** PARTIAL | **0** | 零行 ✅；ext/torch 未 jsync |

## 实验 B5b 状态（ext/torch jsync + hang）

| 轮次 | run_id | localize | E3 头条比 | dense | culprit_rows | 备注 |
|------|--------|----------|-----------|-------|--------------|------|
| **B5b** | `20260728_132612…` | culprit=**7** ✅ | **116.72%** | **0** | **0** | bundle `_sync_live_tracers` ✅；SET_DG 原生 time；**hang@L134**；无 hot-updated log |

## 实验 B5c 状态（短测 smoke）

| 轮次 | run_id | __file__ | rows | hot-updated log | 备注 |
|------|--------|----------|------|-----------------|------|
| **B5c** | `20260728_135724…` | pydeps ✅ sync=True | **4368** | no（次要） | **PASS** → 允许 B5d |

## 实验 B5d 状态（B5c PASS 后全训）

| 轮次 | run_id | localize | E3 头条比 | dense | culprit_rows | 备注 |
|------|--------|----------|-----------|-------|--------------|------|
| **B5d** | `20260728_141052-pillar-c-v3-pr2-e3-b5d` | culprit=**7** | **114.47%** | **1** | **8554** | WINDOW=15s hang=480s SET_DG=1 |

## 实验 B8 状态（B7 code + AVG SQL/window=100 + no-progress kill + HCCL 600s）

| 轮次 | run_id | localize | E3 头条比 | dense | culprit_rows | 备注 |
|------|--------|----------|-----------|-------|--------------|------|
| **B8** | `20260728_204936-pillar-c-v3-pr2-e3-b8` | culprit=**7** ✅ | **88.28%** W* | 16 | **9647** ✅ | grj-w0 · WINDOW=15s · hang=480s · SET_DG=1(time,19s) · avg/100/90s/600s gates · 训练完成 1042 步 · dense=16 与采样架构冲突 |
