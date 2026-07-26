# MECH_FIX · Pillar-C C0（§2.0）

> 更新：2026-07-26 17:22+08。本地代码已落地；**grj-w0 短测 C0-a/C0-b 已绿**。

## C0-a SET→live tracer

| 项 | 状态 |
|----|------|
| 方案 | 训练主线程 `optimizer_step_post_hook` 每步读 `probing.torch.profiling`；spec 变则 `_sync_live_tracers`（不在 Tokio 调 `configure()`） |
| 代码 | `python/probing/ext/torch.py`（已同步两边 `probe-bundle/pydeps`） |
| 集群短测 | ✅ **PASS** @ grj-w0 |

**证据**（`artifacts/c0_mech_20260726_172201/`）：

- 起训 `PROBING_TORCH_PROFILING=on,rate=0.05` → `C0A_TT_BEFORE=29`
- mid：`probing -t $PID config 'probing.torch.profiling=on,rate=1.0'`（读回 `on,rate=1.0`）
- 后续：`C0A_TT_MID=141` → `C0A_TT_AFTER=309`；**DELTA=280**（>> 配置读回 alone）
- 文件：`c0a_counts.txt` / `c0a_set.txt` / `c0a_tt_*.txt` / `c0a_train.log` / `SUMMARY.txt`

## C0-b rate=0

| 项 | 状态 |
|----|------|
| 方案 | `TorchProbeConfig.parse` / `set_sampling_mode` 接受 `rate=0`；`_ensure_step_plan` 永不采样 |
| 代码 | `python/probing/profiling/torch_probe.py`（已同步两边 bundle） |
| 本地 | `on,rate=0` → rate=0；plan `sampled_step=False` ✅ |
| 集群 | ✅ **PASS**：`on,rate=0` 短跑 → `python.torch_trace` **N=0**；`python.torch_step_timing` **N=28** |

**证据**：同目录 `c0b_counts.txt` / `c0b_torch_trace.txt` / `c0b_step_timing.txt` / `c0b_config_torch.txt`（读回 `on,rate=0`）。

## C0-c 追溯窗按步

| 项 | 状态 |
|----|------|
| 方案（本波） | **不新加在线 API**；E1-off 对已有 full `torch_trace` 按 W 截窗重判；标定环形 20MB≈步数 |
| E1-off W* | ✅ 初版见 `pillar_c_v2/E1_off/W_STAR.md`：**P1-SW-C W\*=100**；P3 UNRESOLVED；P1-HW-B NO_W_STAR |
| 标定 | torch_trace 环 **20MB ≈ 546 步**（`rows_ow=0`） |
| 在线按步保留 | ⬜ 正式 E1 若需再加 |

## 放行条件

- [x] grj-w0 短测证明 C0-a 热更后 trace 密度上升  
- [x] grj-w0 短测证明 C0-b rate=0 几乎无 torch_trace  
- [x] E1-off 产出 W* 初版表（可并行）

## 路径

- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/_prep/pillar_c_gate/artifacts/c0_mech_20260726_172201/`
- 本机：`project/probing-huawei/results/ascend-ais/_prep/pillar_c_gate/artifacts/c0_mech_20260726_172201/`
- 日志：`…/logs/c0_mech_20260726_172201.log`
- 脚本：`c0_mech_shorttest.sh`

## 结论

**C0-a / C0-b = PASS → 可放行 E1 / E2**（机制上 SET→live 与 rate=0 已成立）。E1-off / C0-c 标定可并行，不阻塞开扫。
