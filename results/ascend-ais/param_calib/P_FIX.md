# P-FIX · Param-Calib（环容量 + 注入尖刺）

> 运维真相源亦在 `results/ascend-ais/_prep/pillar_c_gate/P_FIX.md`（该目录整体 gitignore）。本文件为入库副本。

## 结果（`p_fix_20260727_003642` · PASS）

| 项 | 结论 |
|----|------|
| cpu.util 环 | 默认 **8 MiB**（`PROBING_CPU_RING_MB`）；span≈36.2 s |
| 注入尖刺 | top_dur=**0.618 s**；n_spikes=**26** |
| SET 键 | `probing.torch.profiling=`（MECH_FIX C0-a，既有） |

代码：`probing/extensions/cc/src/extensions/cpu/collector.rs`。  
脚本：`scripts/fail-slow/param_calib/p_fix_ring_spike_shorttest.sh`。
