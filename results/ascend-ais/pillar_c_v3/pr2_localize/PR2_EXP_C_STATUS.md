# PR-2 实验 C · PASS

**日期**：2026-07-28
**parent**：`20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`
**pod**：`grj-megatron-32card-0716-worker-0`（grj-w0）
**case**：P1-SW-C loud（GPU 编译尖刺 inline_2c，victim=rank 7）

## 头条 · W\* 追溯窗（handbook §2.4 实验 C 主判据）

| 项 | 值 | 判据 |
|----|-----|-----|
| **W\*** | **200** | 目标 =100（handbook 容忍不迟于 200 → **满足**） |
| W=100 evidence | `no_spike:top_step=261:dur_s=0.1996:med=0.1000:n_steps=10` | anchor=282 → 窗 (182,282]，未包含 spike 步 161 |
| W=200 evidence | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` | anchor=282 → 窗 (82,282]，包含 spike 步 161 |
| v2 E1-off 参考 | W*=100 spike@238 AdamW dur≈0.71s | 本轮 spike@161 dur=0.53s module=DistributedDataParallel |

## 五指标

| 项 | 值 | 判据 |
|----|-----|-----|
| 头条比（数据量比 W\* content） | **92.20%** | 目标 <100% (相对 v2 P1-SW-C full_fidelity=1.67GB) |
| dense_ranks | **16** | 各 rank 都有 rows>0（rate=0 也留了每 20 步的稀疏采样）；SET 后目标 rank 7 密度更高（但环冲刷了） |
| culprit_rank (SQL 定位) | **7** | GT=7 ✓ |
| LOCALIZE_FALLBACK | **0** | 目标 =0（SQL 命中） ✓ |
| SET_OK / SET_DOWNGRADE | Y / **1** | reason=time (window_s=15 短) |
| inject_stop marker (step 300) | 1 | 训练完成 ITERS=1000（rank_0000.jsonl=1000 行）; marker 逻辑不触发但训练完整 |

## 关键证据

**duration_spike 复现**：
- spike step=**161**（inject_stop=300 之前），dur_s=**0.5289**（threshold=0.40 ✓；ratio=5.24× median 0.101 ✓）
- module=**DistributedDataParallel**（P1-SW-C 编译尖刺注入模块）
- 与 v2 E1-off P1-SW-C W*=100 (spike@238 AdamW dur=0.71s) 差异：本轮 spike 移到 step 161（inline_2c 首次编译点），module 是 DDP forward wrapper 而非 AdamW，dur 也偏低但仍 >2× threshold。

## B8 三处 gate（保留）

- PILLAR_C_LOCALIZE_STEP_AGG=**avg** · PILLAR_C_LOCALIZE_STEP_WINDOW=**100**
- HCCL_EXEC_TIMEOUT=**600**
- PILLAR_C_NO_PROGRESS_KILL_S=**90**

## B6 gates

- PROBING_TORCH_COMM_COLLECTIVE_LAZY=**1** · PROBING_TORCH_STEP_TIMING_LAZY=**0**
- PILLAR_C_PRUNE_EXTRA_PIDS=**1** · DRY=**0**

## 结论

- **PASS**（W\* 达成 handbook §2.4 判据；W=200 首个 enough=Y，容忍窗内；主证 torch_trace.duration_spike step 161 dur=0.529 module=DDP）
- 数据量比 92.2%（相对 v2 full_fidelity 上界；因 rate=0 也留了每 20 步的稀疏采样，加上 rank 7 SET 后短暂密采，各 rank 20MB 环都写满）
- **与 v2 UNRESOLVED 对照**：v2 `pillar_c_v2/20260726_173830-pillar-c-e1-p1-sw-c-loud` 用旧键 `torch.profiling=` → NO_W_STAR；本轮用统一后的 `probing.torch.profiling=` → **OK W\*=200**。SET 键名统一是 PR-2 关键修复。
- **与 E1-off W\*=100 差异**：本轮 anchor_step=282（非 300，因环 ≤300 部分只有 30 unique 步），使 W=100 窗 (182,282] 恰好错过 spike@161；若 anchor=261 则 W=100 命中。整体证据链完整，仅"窗尺"未取到最紧。

- W\* 判分：`PR2_TRACEWINDOW_P1SWC.md` / `E1_WINDOW.json`
- 数据量比：`PR2_EXP_C_RATIO.md` / `PR2_EXP_C_RATIO.json`
- 训练完整跑到 1000 步；SET_DG_OK；LOCALIZE_FALLBACK=0；culprit=7 命中 GT
