# PARAM · ③-B 升精度生效延迟

> case=`P3-SW-A` · parent=`20260727_023223-3b-p3-sw-a-loud` · 测响应时间（无自变量扫）
> SET rate=`1.0` · SET_SCOPE=victim · W*=100 · TT_floor=800

## 结论

- **生效延迟（SET→live）上界** = **≤12 步**（SET_L=130 → 首见 TT@L=142；含探针 jsync 滞后；机制下界 ≈1 步）
- **升完到够归因（够 TT_floor=800）上界** = **≤12 步**（首轮 probe 已 n=1821）
- **W\* 跨度**：本轮 **不作数**（首 tick `gmin=0` 伪跨度）
- **总响应上界** = **≤12 步** vs 对手重启 ≈ **150** 步（≥**12.5×**）
- SET_OK=Y · probe=Y · 其后训程 `node_0.fail`/137（jsonl≈250）不影响已记延迟

## 对照表

| 量 | 步数 |
|---|---:|
| SET→首见 TT（上界） | 12 |
| SET→够 TT_floor（上界） | 12 |
| live→够 W* | —（本轮无效） |
| 机制 SET→live（post-hook） | ≈1 |
| 对手重启（S1） | 150 |

## 这数据证明为什么这么设

对 P3-SW-A loud：常驻 rate=0 → SET `probing.torch.profiling=on,rate=1.0`（SET_SCOPE=victim；在线 probe）。SET→够 TT 上界 ≤12 步（<<150），热升详无需重启。

## 证据

- set_upgrade: `.../C2_probing/set_upgrade.log`（SET_L=130, SET_LATENCY_MS=1145）
- latency_probe: `.../C2_probing/set_latency_probe.log`
- S1 对照: `project/probing-huawei/results/ascend-ais/pillar_c_v2/S1_MID_ATTACH.md`
- 机制: optimizer post-hook 每步读 `probing.torch.profiling` → `_sync_live_tracers`

## 产物

- `results/ascend-ais/param_calib/3B_upgrade_latency/PARAM.json`
- `results/ascend-ais/param_calib/3B_upgrade_latency/20260727_023223-3b-p3-sw-a-loud/`
