# PR-2 实验 B5 · 诊断（B4 rate=0 → 729 行）

1. **B4 现象**：16 rank 各 `python.torch_trace` **729 行 / 20MB 满环**；v2 E3 同配 `on,rate=0` 仅 culprit **54054 行**，其余 rank **n_rows=0**（空环）。
2. **根因**：bundle 内 `torch_probe.py`（PR-1）把 `rate<=0` 改成每 `PROBING_TORCH_MIN_STEP_INTERVAL`（默认 500）步写**全模块稀采锚点**（9 步×~81 层≈729 行），非手册「零行」；`SET_DOWNGRADE rate=0` 只停新写，**不回收**已落环数据。
3. **与 v2 差异**：v2 C0-b 约定 `rate=0` → `torch_trace N=0`；PR-1 稀疏锚点未进 launch 配置且与 E3 头条尺冲突；B3/B4 其余项（localize、原生降回）已通。
4. **B5 改法**：`torch_probe` 默认 `rate=0` **永不采样**；稀采锚点改 opt-in `PROBING_TORCH_SPARSE_ANCHOR=1`；jsync 至 probe-bundle；其余同 B4（localize+culprit SET+30s 降回）。
5. **B5 跑后（130247）**：零行 **✅**（16/16 `n_rows=0`）；culprit SET 窗仍 0 行 → bundle `ext/torch.py` **未 jsync**（Jul-27 无 C0 热更）→ **B5r2 需 jsync ext/torch.py**；空环 W* 评分已按 0 内容（114.99%）。
