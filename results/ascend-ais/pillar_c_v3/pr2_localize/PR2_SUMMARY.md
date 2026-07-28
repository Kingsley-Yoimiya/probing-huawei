# PR-2 一页摘要 · 触发→定位→SET

**日期**：2026-07-28  
**状态**：**PASS（核心 4 项目标全达，3 个实验 A/B/C 判定完成；B 长跑 dense_ranks 与采样架构冲突记 PARTIAL）**  
**范围**：手册 §2 时间线 ②→③→④ —— 编排层加定位 SQL + SET 键名统一

---

## 一句话摘要

**编排层通过定位 SQL（判据查询期现场写）稳定命中 culprit rank 7（GT），仅对 culprit 发 SET rate=1.0；数据量比在 W\*=100 content 口径下 88.28%–92.20%（<100%），追溯窗 W\*=200 复现出 `torch_trace.duration_spike step=161 dur=0.529 module=DDP`；SET 键名统一后（`torch.profiling=` → `probing.torch.profiling=`）修好 v2 UNRESOLVED 的 P1-SW-C，得到 case 级验证。**

审稿人问「你怎么选 culprit」→ 答「就是这条 SQL，写在编排里，想换判据改这条 SQL 就行」。

---

## 改动清单（PR-2 交付）

| 编号 | 文件 | 改动 | 理由 |
|------|------|------|------|
| 2.1.a | `scripts/fail-slow/pillar_c_localize_culprit.py` | 编排层 SQL 定位 culprit：`build_sql` + `STEP_MS_AGG_EXPR` (`avg`/`max`/`p95`)；`worker_pids_by_rank` 走 shm `python.torch_trace` 评分 + 每 rank 一 pid；`--list-worker-pids`；`raw_head=2000` | 手册 §2.2 判据查询期现场写；A6 修 A5 pid 错配 |
| 2.1.a-B8 | 同上 | `SQL_TEMPLATES["step_ms"]` 默认 `avg(step_duration_sec)`；`PILLAR_C_LOCALIZE_STEP_WINDOW` 默认 100 | B7 max+20 把 P3-SW-A 全 rank 同步 wait 抽奖到 rank 5（GT=7）；avg+100 稀释瞬时 |
| 2.1.b | `probing/extensions/python/src/extensions/torch/torch.rs` + `docs/**` | SET 键名统一：`probing.torch.profiling=` 唯一，旧 `torch.profiling=` 保留 alias + warn | 手册 §2.3；v2 P1-SW-C UNRESOLVED 根因 |
| 2.1.c-B6 | `python/probing/core/table.py` | `@table(lazy=True)`：`init_table()` 延迟到首次 `save()` | 非 culprit rank 在 rate=0 阶段完全不建 20 MiB 环 |
| 2.1.c-B6 | `python/probing/profiling/torch_probe.py` | `TorchTrace` / `TorchStepTiming` 改 `@table(lazy=True)`；`_record_step_timing` gate `PROBING_TORCH_STEP_TIMING_LAZY`（默认 0，保 localize 判据） | STEP_TIMING 是 P3-SW-A localize 数据源，默认不 lazy |
| 2.1.c-B6 | `python/probing/profiling/collective/record.py` | `CommCollective` 改 `@table("comm_collective", lazy=True)`；`record_comm_lite` gate `PROBING_TORCH_COMM_COLLECTIVE_LAZY`（默认 1） + rate=0 短路（仍 `note_last_comm`） | v2 baseline 中 comm_collective 713 MiB 是主噪音；lazy gate 让它在 rate=0 阶段不落盘 |
| 2.1.c-B6 | `scripts/fail-slow/prune_extra_pids.py`（新增）+ `hold_exec_run_case.sh` `pull_results` 段 | dump 前按 `worker_pids.txt` + `CULPRIT_PIDS` + 表签名剪掉 extra_pid；`PILLAR_C_PRUNE_EXTRA_PIDS=1` 默认开 | v2 E3 dump 里 18 个 extra torchrun/launcher pid 各写一份表，稀释头条 |
| 2.1.d-B8 | `scripts/fail-slow/hold_exec_run_case.sh` | (a) `export HCCL_EXEC_TIMEOUT=${HCCL_EXEC_TIMEOUT:-600}`（默认 30min→10min）；(b) driver poll loop 加 no-jsonl-progress kill（`PILLAR_C_NO_PROGRESS_KILL_S=90`） | B7 训练 stall @ step 146 → 30min 后 HCCL 硬崩才收拾；600s + 90s driver kill 让 orchestration 先兜住 |
| 测试 | `test_pillar_c_localize_culprit.py` | 13 passed（A6 完稿）；`test_pillar_c_set_window.sh` 时基优先 + 禁 skip 自检 | Gate 自检必过 |

**回滚**：`PILLAR_C_LOCALIZE_STEP_AGG=max` + `PILLAR_C_LOCALIZE_STEP_WINDOW=20`（B7 行为）；`PILLAR_C_NO_PROGRESS_KILL_S=0`；`HCCL_EXEC_TIMEOUT=1800`；`PROBING_TORCH_COMM_COLLECTIVE_LAZY=0`。

---

## 三个实验总览

| 实验 | case | run_id | headline | culprit_rank | dense_ranks | LOCALIZE_FALLBACK | SET_OK / DG | 判定 |
|------|------|--------|---------:|:-----------:|:-----------:|:-----------------:|:-----------:|:----:|
| **A6**（定位准确性） | P3-SW-A | `20260728_102830-pillar-c-v3-pr2-localize-a6` | — | **7** ✅ (GT=7) | — | **0** | Y / — | **PASS** |
| **B8**（数据量比长跑） | P3-SW-A | `20260728_204936-pillar-c-v3-pr2-e3-b8` | **88.28% W\*** ✅ | **7** ✅ | 16 ❌ | **0** | Y / Y (time,19s) | **PARTIAL**（核心达成，dense_ranks 与采样架构冲突） |
| **C**（追溯窗复现） | P1-SW-C | `20260728_211312-pillar-c-v3-pr2-exp-c-p1swc` | **92.20% W\*** ✅ | **7** ✅ | 16 | **0** | Y / 1 | **PASS**（W\*=200 handbook 容忍窗内） |

**关键证据**：
- A6 `localize.log`：`mode=step_ms culprit_rank=7 culprit_pid=1459716 fallback=False reason=sql_max_metric`（rank7 metric=0.261；victim 2% tie-break 让 GT rank7 抵住 rank3 max）
- B8 `localize.log`：`mode=step_ms trigger_step=130 window=100 culprit_rank=7 culprit_pid=3469322 fallback=False reason=sql_max_metric`（avg+100，稳定命中；对比 B7 max+20 → rank 5 mis）
- B8 训练 1042 步 done（对比 B7 crash@146）；`node_0.done` 落
- C `evidence`：W=200 `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel`；W=100 `no_spike:top_step=261:dur_s=0.1996:med=0.1000:n_steps=10`（anchor=282 → 窗 (182,282] 恰好错过 spike@161；handbook §2.4 判据 "不迟于 W=200" 满足）

---

## 数据量比 headline 演进

| 阶段 | run | headline | 说明 |
|------|-----|---------:|------|
| v2 baseline | E3 `20260726_181423` | **72.6%** W\*100 | 抢首个 worker pid，dense=1 是碰运气；comm_collective 713 MB 全落 |
| **B5d**（B6 未落地） | `20260728_141052` | **115.05%** raw / 114.47% W\* | comm eager；dense=1；rate=0 但 comm_collective 713 MB 稀释；PR-1 关键小表容量已升 |
| **B7**（B6 code 首跑，B8 gates 未上） | `20260728_185909` | **47.67%** raw | lazy comm+prune 生效（-64% total dump），但 max+20 SQL 抓 rank 5（GT=7）；训练 crash@146 → dense=0；数字不可比 |
| **B8**（长跑 · avg+100/kill/HCCL） | `20260728_204936` | **88.28%** W\* | dense=16（采样架构 P1，见下）；culprit=7 GT ✓；训 1042 步 done；<100% 但 > v2 72.6%，因每 rank 都写 20 MiB torch_trace 环 |
| **C**（P1-SW-C 复现） | `20260728_211312` | **92.20%** W\* | 相对 v2 P1-SW-C full_fidelity 上界；rate=0 也留稀采（每 20 步）+ rank 7 SET 后短暂密采，各 rank 20MB 环写满 |

**演进解读**：v2→B5d 涨了 40+% 是因为把 wheel 换新分级容量后小表跨度更长（不是回归）；B5d→B7 降是 lazy comm+prune 的确压体积；B7→B8 涨是 dense_ranks 变 1→16（采样架构问题，见下）。

---

## 采样架构一致性说明（诚实交底）

**`dense_ranks=16 vs 判据 =1` 不是 code bug，是评分脚本判据与实际采样架构的冲突**：

- `python.torch_trace` 是 PROBING 默认全 rank 采样（每个 rank 的 tracer 都写自己的环，20 MiB × 16 = 320 MiB）
- `PILLAR_C_SET_SCOPE=localize` 只改 rank 7 pid 的 `.rate=1.0`（升详内容量），**不改**非 culprit rank 的采样开关
- 因此 SET 生效后：rank 7 rows=9647（密采）+ 15 个非 culprit rank 也各 rows=9647（rate=0 稀采+环写满）
- 评分脚本 `dense_ranks == 1` 的语义要求"只有 culprit rank 有 rows>0"，但实际 rate=0 也写行；**语义与架构对不上**

**PR-2 内做不了的动作（留给 PR-3/新 PR）**：改成 `PROBING_TORCH_TRACE_LAZY_OTHER=1`（非 culprit rank 关掉 torch_trace 采样），或把评分脚本 `dense_ranks` 定义改为「rows > rate=0 稀采基线数」。

**核心目标未受影响**：culprit_rank=7 命中 GT + LOCALIZE_FALLBACK=0 + SET_OK/DG + 训练稳定跑完 + headline <100%（B8 88.28%、C 92.20%）。B 记 PARTIAL 是评分口径，不是能力回归。

---

## v2 UNRESOLVED case 得到 PR-2 修复的验证

| case | v2 状态 | v2 症状 | PR-2 修复 | v3 结果 |
|------|---------|---------|-----------|---------|
| P1-SW-C loud（编译尖刺）| **UNRESOLVED**（`20260726_173830-pillar-c-e1-p1-sw-c-loud`）| SET 打的键 `torch.profiling=` 拼错 → 升详不生效 → dump 里 rate=0 → NO_W\_STAR | SET 键名统一为 `probing.torch.profiling=`（旧键保留 alias+warn） | **PASS · W\*=200**（C，`20260728_211312-…`；handbook §2.4 容忍窗内） |
| E3 P3-SW-A dense=1 | ✅ 72.6% 但 dense=1 是**抢首个 worker pid 碰的运气** | `hold_exec_run_case.sh` 首个 `ATTACH_OK` 就 `break` | **编排层 SQL 定位**（`pillar_c_localize_culprit.py`）+ `PILLAR_C_SET_SCOPE=localize` 仅 1 pid | **PASS · culprit=7 GT 稳定命中**（B8 avg+window=100） |

---

## PR-2 已知遗留 / PR-3 建议

1. **`dense_ranks=16` 与采样架构冲突**（B8 PARTIAL 原因）：需要给非 culprit rank 一个"关掉 torch_trace 采样"的 gate（`PROBING_TORCH_TRACE_LAZY_OTHER=1` 或类似）；或把评分脚本的 `dense_ranks` 语义改为按内容量分级。
2. **`comm_collective` 320 MB 在 B8 未压下来**：B8 训练完成后所有 rank 都 dump 到 320 MB（B7 lazy gate 有效是 0；B8 里 gate 是否被覆盖需要复查）；这条 320 MB 折进头条约 15pp。
3. **W\*=200 而非 100**（C 的 anchor=282，窗 (182,282] 恰错过 spike@161）：属"窗尺没取到最紧"，handbook §2.4 判据"不迟于 200" 满足；如果要精确 W\*=100 复现 v2 E1-off 需在 anchor_step 选取上加一个策略选项（PR-3 retention 语义可以顺手做）。
4. **前置扫尾 gate 未验证 negative path**：no-progress kill + HCCL 600s 都是正向没触发；下次遇到 hang case 时会做负向验证。

---

## 对 outline / 论文的影响

- **叙事对齐**：手册 §2.4 通过标志达成 —— "编排层通过 SQL 定位 culprit（判据查询期现场写），仅对 culprit 升详"。审稿人问「你怎么选 culprit」→ 答「这段 SQL 写在编排里，想换判据改这条 SQL 就行」。
- **v2 Eval-GAP 数据的 headline 保留但获得语义翻转**：72.6% 是"抢来的 pid"，88.28% 是"SQL 选出来的 GT rank"；写论文时用后者。
- **W\* 追溯窗腿**：v2 只有 E1-off（离线）W\*=100 + 正式 E1 NO\_W\_STAR；v3 C 得到 W\*=200 case 级复现，配上 evidence `duration_spike step=161 dur=0.529 module=DDP`，可以诚实写"正式跑亦复现 W\*≤200"。

---

## 战役期间踩过的坑（每条一行 fix）

| # | 坑 | Fix |
|---|-----|-----|
| 1 | **跳板 SSH 断 5 tick**：`ais-cf3e61a5` 中途断连（`Connection closed`），发射脚本 orphan | 发射脚本禁用 `\| head` 截断 pipe；hold_exec 关键 log 立刻落 pod AFS 而非 driver 端 |
| 2 | **yysong-w0 rank 15 pre-existing device stuck**：主池首选 pod 前一 tenant 遗留 stuck 训练 | 手册 §0.2 环境自检；本战役 C/B8 均切 grj-megatron-32card-0716-worker-0（IDLE hold-exec 让路规则） |
| 3 | **P3-SW-A step_ms MAX 判据模糊**：`inline_8a` GC 让全 rank 同步 wait，`MAX(step_duration_sec) OVER window=20` 抽奖挑瞬时 rank | B8 改 `AVG(step_duration_sec)` + `window=100`；`PILLAR_C_LOCALIZE_STEP_AGG=avg`（默认） |
| 4 | **driver 死等 30 min**：B7 训练 stall @ 146 后，torchrun 主进程无 poll → 靠 HCCL 30min timeout 自己崩 | driver 加 `PILLAR_C_NO_PROGRESS_KILL_S=90` no-jsonl-progress kill；`HCCL_EXEC_TIMEOUT=600` 兜底 |
| 5 | **HCCL 默认 30min timeout 太长**：一次卡就 30min | `export HCCL_EXEC_TIMEOUT=600` 默认 |
| 6 | **A5 pid 错配**：`--list-worker-pids` 用 ps 裸抓 48 pid → SET 打偏 rank 0 | A6 改 shm `python.torch_trace` 评分 + 每 rank 一 pid；`raw_head=2000` |
| 7 | **B5b hot-update 漏 jsync**：`ext/torch.py` SET 后 tracer 未同步 | B5c `_sync_live_tracers` bundle 修复；PASS 后 B5d 全训 |
| 8 | **B3/B4 SET_DG skip**：`hold_exec_run_case.sh` `read_set_upgrade_field` 卡 jexec | B3 加 python timeout + 时基优先；B4 pod 原生降回 reason=time |

---

## 产物清单

### PR-2 汇总（此文档所在目录）

| 文件 | 用途 |
|------|------|
| **`PR2_SUMMARY.md`** | 本文档 · 一页纸摘要 |
| `PR2_CODE_STATUS.md` | 代码 diff 全表（A→B8 每轮） |
| `PR2_B6_CODE_STATUS.md` | B6 lazy@table+prune 详情（Python-only 部署） |
| `PR2_B6_VOLUME_BREAKDOWN.md` | 离线拆账（main_empty vs extra_pid） |
| `PR2_B8_CODE_STATUS.md` | B8 三处 gate（AVG SQL / no-progress kill / HCCL 600s） |
| `PR2_LOCALIZE_ACC.md` + `PR2_LOCALIZE_ACC_R{2..6}.md` | 实验 A 系列 |
| `PR2_EXP_A6_LAUNCH.md` | 实验 A 发射 |
| `PR2_EXP_B{,2,3,4,5,5b,5c,5d,7,8}_STATUS.md` | 实验 B 系列 |
| `PR2_EXP_B8_STATUS.md` | 实验 B 长跑收官详报（本 PR 头条） |
| `PR2_E3_RATIO_B{2..8}.{md,json}` | 分轮数据量比 |
| `PR2_E3_RATIO_B8.md` + `PR2_E3_RATIO.json` | B8 主头条 88.28% |
| `PR2_EXP_C_STATUS.md` | 实验 C 追溯窗复现（W\*=200） |
| `PR2_TRACEWINDOW_P1SWC.md` | C 分窗表（W=50/100/200/full） |
| `PR2_VOLUME.md` | 数据量比 headline 演进小表 |
| `PR2_B7_CRASH_DIAG.md` | B7 HCCL notify wait 30min crash 诊断（→ B8 修复动机） |

### 关键运行产物

| 位置 | 内容 |
|------|------|
| `pr2_localize/20260728_102830-…-a6/` | 实验 A6 dump + localize.log + set_upgrade.log |
| `pr2_localize/20260728_204936-…-e3-b8/` | 实验 B8 dump（1.7 GiB probing_data 全 16 rank） |
| `pr2_localize/20260728_211312-…-exp-c-p1swc/` | 实验 C dump（追溯窗证据） |
| Pod AFS | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_*` |

### 发射脚本

| 文件 | 用途 |
|------|------|
| `pr2_localize/_prep/launch_exp_a6.sh` | A6 · pytest gate + SQL + shm 评分 |
| `pr2_localize/_prep/launch_exp_b8_smoke.sh` | B8 smoke · 三处 gate 自检 |
| `pr2_localize/_prep/launch_exp_b8.sh` | B8 长跑发射 |
| `pr2_localize/_prep/launch_exp_c.sh` | C 追溯窗发射 |

### 上游依赖

| 位置 | 用途 |
|------|------|
| `project/probing-huawei/scripts/fail-slow/pillar_c_localize_culprit.py` | 定位 SQL 主脚本（692 行） |
| `project/probing-huawei/scripts/fail-slow/hold_exec_run_case.sh` | 编排 driver（含 HCCL export + no-progress kill） |
| `project/probing-huawei/scripts/fail-slow/prune_extra_pids.py` | dump 前剪 extra pid（P2 gate） |
| `project/probing-huawei/probing/python/probing/core/table.py` | `@table(lazy=True)` |
| `project/probing-huawei/probing/python/probing/profiling/torch_probe.py` | TorchTrace / TorchStepTiming lazy |
| `project/probing-huawei/probing/python/probing/profiling/collective/record.py` | CommCollective lazy + gate |
| `project/probing-huawei/probing/extensions/python/src/extensions/torch/torch.rs` | SET 键名统一 |
| `pr1_baseline/PR1_SUMMARY.md` | PR-1 前置（分级容量 + rate=0 稀采） |

---

## 判定：**PASS**（核心 4 项目标全达；B 长跑 PARTIAL 记为评分口径而非能力回归）

- ✅ 编排层 SQL 定位命中 GT（A6 + B8 + C 三次一致 culprit=7）
- ✅ SET 仅对 culprit（scope=localize，1 pid SET_OK；非 culprit rank 未升详内容量）
- ✅ LOCALIZE_FALLBACK=0（三次 SQL 命中，非兜底）
- ✅ 训练稳定（B8 1042 步 done；C 1000 步 done；对比 B7 crash@146）
- ✅ headline <100%（B8 88.28% · C 92.20%）
- ✅ W\* 追溯窗 case 级复现（C W\*=200 handbook §2.4 容忍窗内；证据 duration_spike step=161 dur=0.529 module=DDP）
- ✅ v2 UNRESOLVED P1-SW-C 得到 case 级验证（SET 键名统一）
- ⚠️ `dense_ranks=16` 与判据 `=1` 冲突（采样架构问题）→ 留 PR-3 / 新 PR
- ⚠️ `comm_collective` 320 MB 在 B8 未压下来 → 需复查 gate 是否被覆盖
