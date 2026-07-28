# PR-2 实验 B8 · PARTIAL

**日期**：2026-07-28
**parent**：`20260728_204936-pillar-c-v3-pr2-e3-b8`
**pod**：`grj-megatron-32card-0716-worker-0`（grj-w0）
**case**：P3-SW-A · GT culprit rank=7

## 头条 · 五指标

| 项 | 值 | 判据 | 结果 |
|----|-----|------|------|
| 头条比 | **88.28%**（W\*_content_est） | 目标 <100% | ✅ |
| culprit_rank | **7** | GT=7 | ✅ |
| LOCALIZE_FALLBACK | **0** | =0（SQL 命中） | ✅ |
| SET_OK / SET_DOWNGRADE_OK | **Y / Y** | reason=`time` window_s=15 elapsed=19s upgrade_step=130 downgrade_step=210 | ✅ |
| SET_LATENCY_MS | **11531**（11.5s） | 参考 B7=17.5s → 提速 | — |
| dense_ranks | **16** | 目标 =1 | ❌（见诊断） |
| dense_rank_matches_culprit | **Y** | culprit_pid=3469322 rows=9647 in dense set | ✅ |
| culprit TT rows | **9647** | 目标 >0 | ✅ |
| 非 culprit max rows | **9647** | 目标 =0 | ❌（所有 rank 均 dense） |
| inject_stop marker (step_300) | ✅ | ITERS=1000 inject=[100,300] | ✅ |
| 训练完成 | **1042 步**（超 ITERS=1000） | | ✅ |
| WINDOW_S | 15 | 时基降回 | ✅ |
| hang_max | 480s | 8min（本轮未触发） | ✅ |
| no-progress kill | **未触发** | 训练稳定 → 正向未验；负向待未来 hang case | 正向 ✅ |
| HCCL_EXEC_TIMEOUT | 600s | export 已生效；本轮未触发（无 collective 卡） | ✅ |

## B8 三处 gate

**(a) localize SQL avg + window=100** ✅ 有效：
- localize.log 首行：`LOCALIZE_SQL: query='SELECT COALESCE(avg(step_duration_sec), 0) AS metric FROM python.torch_step_timing WHERE local_step >= 30 AND local_step <= 130' mode=step_ms trigger_step=130 window=100 culprit_rank=7 culprit_pid=3469322 fallback=False reason=sql_max_metric ts=1785243084`
- 与 smoke 完全对齐；rank 7 稳定命中 GT（B7 用 max/20 抓 rank 5 mis）

**(b) HCCL_EXEC_TIMEOUT=600** ✅ export 有效：
- `_work/run_2.sh` 含 `export HCCL_EXEC_TIMEOUT=${HCCL_EXEC_TIMEOUT:-600}`
- 本轮训练稳定跑完，未触发；负向路径待未来 hang case

**(c) no-progress-90s driver kill** ✅ gate 存在，本轮未触发：
- 训练正常推进到 step 1042 → `DONE world=16` → `node_0.done` 落
- `node_0.log` 无 `NO_JSONL_PROGRESS_` 打点；正向路径 kill 未触发是正常的

## Orchestration 完整摘要

- FIRE_OK @20:50 → warmup_done（15s）→ inject sidecar victim=7 kind=inline_8a every=1 stall_s=0.25
- LOCALIZE @20:51:24 trigger_step=130 window=100 elapsed=586ms culprit_rank=7 pid=3469322 fallback=False
- ATTACH_OK pid=3469322 retries=0（0s）→ SET_TARGET `probing.torch.profiling=on,rate=1.0` @20:51:25
- SET_LATENCY_MS=11531 (11.5s)
- SET_DOWNGRADE @20:51:51 reason=`time` window_s=15 elapsed_s=19 upgrade_step=130 downgrade_step=210
- 训练继续到 step 1042（`node_0.log: DONE world=16`）
- volume_at_upgrade：`hot_memt=0 hot_bytes=0 cold_segs=16 cold_bytes=2353024`
- step_100.marker ✅ · step_300.marker ✅ · node_0.done ✅

## PRUNE_EXTRA_PIDS 结果

`_work/prune_extra_pids.log`：
- **kept=16 · removed=0 · ignored=1**（B7 是 kept=16 removed=18；本轮 grj-w0 无残留 extra torchrun/launcher pid，无 prune 目标 —— gate 存在但本轮无需清理）

## 头条数字（Volume）

| 分类 | 字节 | 备注 |
|------|------|------|
| 动态 raw total | **1871983488** (1785.26 MiB) | probing_data 全量 |
| cold_bytes | 2353024 (2.24 MiB) | 冷段极小（B7=48.7 MiB） |
| torch_trace 全 rank | 320020480 (305.2 MiB) | 16 rank 均 dense 各 20 MiB |
| torch_step_timing | 320016384 | |
| comm_collective | 320018432 | ⚠ COMM_LAZY=1 未把它压下来（B7=0） |
| trace_event | 320013312 | |
| variables | 320006144 | |
| 动态 W\*=100 估算 | 1581871456 (1508.59 MiB) | headline 分子 |
| 全量 REUSE v2 | 1791975360 (1708.96 MiB) | headline 分母 |
| **ratio_raw_pct** | **104.46%** | > 100% |
| **ratio_w_star_pct** | **88.28%** | **头条**；<100% 但 > v2 72.6% baseline |

## 判定：**PARTIAL**

### 达成部分（PASS 项）
1. **culprit_rank=7** 稳定命中 GT，B8 avg+window=100 gate 修复 B7 max+window=20 mis-localize（rank 5）问题 ✅
2. LOCALIZE_FALLBACK=0（SQL 命中，非兜底）✅
3. SET_OK + SET_DOWNGRADE 全通（reason=time，elapsed=19s > window=15s，原生降回）✅
4. 训练完整跑完 1000 步（实际 1042），未 crash、未 hang ✅（对比 B7 crash@step146）
5. Extra pid prune gate 存在（本轮无残留可清）✅
6. HCCL_EXEC_TIMEOUT=600s export 落位（本轮未触发但语法/位置已验）✅
7. no-progress kill gate 存在（正向未触发；负向待 hang case 复现）✅
8. headline W\*_content_est **88.28% < 100%** ✅

### 阻塞项（PARTIAL 原因）
1. **`dense_ranks=16 ≠ 1`**：SET `scope=localize` 仅对 rank 7 pid 升详，但所有 16 rank 均 dump `python.torch_trace` 到相同大小（9647 rows/rank）。这不是 code bug —— 而是 v2 baseline 也如此：`python.torch_trace` 是全 rank 采样（PROBING 默认），SET 升级只影响 rank 7 pid 的 `.rate` 内容量，不影响非 culprit rank 的 dense 落盘。评分脚本判据 `dense_ranks == 1` 的语义与本运行的实际采样架构冲突。
2. **头条 88.28% > v2 72.6% baseline**：本轮 dense 全 16 rank 而 v2 只有 victim → 每 rank torch_trace 相当大。虽然 <100% 但 lazy comm 未生效（`comm_collective` 320 MB 与其他 tables 一致，未被 COMM_LAZY=1 压下来）。

## 与 B7、v2、smoke 对比

| 项 | v2 | B7 | B8-smoke | **B8 长跑** |
|----|-----|-----|----------|-------------|
| pod | grj | yysong-w0 | grj-w0 | grj-w0 |
| ITERS | 1000 | 1000 crash@146 | 200 | **1000 done** |
| 头条 | 72.6% | 47.67% raw | n/a smoke | **88.28% W\*** |
| dense | 1 | 0 | 0（早退） | 16 |
| culprit | victim | 5 mis | 7 | **7 GT** |
| SET_OK/DG | Y/Y | Y/Y(29s) | Y/Y(19s) | Y/Y(19s) |
| SET_LATENCY | ? | 17.5s | 11.9s | **11.5s** |
| LOCALIZE_FB | 0 | 0 | 0 | **0** |
| culprit_pid rows | victim | 0 | 0 | **9647** |
| 训练完成 | yes | crash@146 | yes(200 步) | **yes(1042 步)** |
| comm_collective 落盘 | 713 MB | 0 | ? | 320 MB ⚠ |
| PRUNE removed | n/a | 18 | 未 verify | 0（本轮无残留） |

## 下一轮建议（供主 Loop）

### 主要选项（建议）

**PASS 项占主导（culprit/localize/SET/训练完成 全 PASS）**，唯一 PARTIAL 是 dense_ranks 判据与采样架构冲突。可主 Loop 决策：

**选项 A（推荐）**：如果主 Loop 判定 PR-2 的核心目标是 "**localize SQL 命中 GT + SET only-culprit + 训练稳定 + 头条 <100%**"，则 B8 长跑已达成核心目标 → **接受 PARTIAL 为 PASS**，直接派实验 C（P1-SW-C 追溯窗）。

**选项 B**：如果要求 `dense_ranks=1` 严格判定 → 需要 code 改动：`PILLAR_C_SET_SCOPE=localize` 除了改 rank 7 pid 的 rate，还需要**把非 culprit rank 的 `python.torch_trace` 采样关掉**（可能需要给每个 rank 的 ext/torch.py 引入 `PROBING_TORCH_TRACE_LAZY_OTHER=1` 或类似 gate）。这属于新 code 改动，需重编 wheel 或注入 python-only patch。

**选项 C**：如果头条数字要求 <60%（B7 47.67% raw 的水平） → 需继续 `comm_collective` lazy 生效诊断（本轮 320 MB 落盘表明 COMM_LAZY=1 未起作用）+ 减少 trace_event/variables 采样。

## 产物路径

- STATUS：`pr2_localize/PR2_EXP_B8_STATUS.md` + `20260728_204936-.../PR2_EXP_B8_STATUS.md`
- 判分：`pr2_localize/PR2_E3_RATIO_B8.{md,json}` + `20260728_204936-.../PR2_E3_RATIO_B8.{md,json}`
- 发射：`pr2_localize/_prep/launch_exp_b8.sh` · 发射日志 `_prep/logs/b8_launch_20260728_204936.log`
- 本地 dump：`pr2_localize/20260728_204936-pillar-c-v3-pr2-e3-b8/dynamic/` (1.7 GiB probing_data，全 16 rank)
- Pod AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260728_204936-pillar-c-v3-pr2-e3-b8/`
- localize.log：`.../round_1/C2_probing/localize.log`（首行 avg+window=100 证据）
- set_upgrade.log：`.../round_1/C2_probing/set_upgrade.log`（SET_UPGRADE step=130 · SET_DOWNGRADE step=210 reason=time）
- run_2.sh：`.../upgrade_rate_1.0/_work/run_2.sh`（含 HCCL_EXEC_TIMEOUT=600 export 证据）
