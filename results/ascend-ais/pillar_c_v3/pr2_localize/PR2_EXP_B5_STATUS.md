# PR-2 实验 B5 · PARTIAL

**parent**：`20260728_130247-pillar-c-v3-pr2-e3-b5`

| 项 | 值 |
|----|-----|
| 头条 W* | **114.99%** |
| dense | **0** |
| culprit | **7** ✅ |
| SET_DOWNGRADE | 原生 **time** ✅ |

**根因**：bundle `ext/torch.py` 无 C0 热更 → SET rate=1.0 未同步 tracer（`torch_probe` 零行修复本身 OK）。

详见 `20260728_130247-pillar-c-v3-pr2-e3-b5/PR2_EXP_B5_STATUS.md`
