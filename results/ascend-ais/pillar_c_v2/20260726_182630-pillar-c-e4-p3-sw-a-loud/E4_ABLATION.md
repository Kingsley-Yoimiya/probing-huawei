# E4_ABLATION · 朴素砍量反例（证「省量必须配触发升详」）

> case=`P3-SW-A` loud · parent=`20260726_182630-pillar-c-e4-p3-sw-a-loud` @ grj-w0
> 砍量臂：常驻 rate=`0` · SAMPLE_MS=500 · **PILLAR_C_SET_UPGRADE=0**（无 mid SET）
> 正例：E3 动态臂 `20260726_181423-pillar-c-e3-p3-sw-a-loud` / `rate_0`（rate=0→SET↑）
> **判分**=采集内容够不够「完整动态路径」归因（P3：`cpu.utilization_rss` ∧ SET↑）；辅尺=总落盘；**禁止**只报 cold / **禁止**训练 step_ms。

## 结论：PASS —— 相对 E3 动态臂 **掉级**

- 完整动态路径够归因（RSS∧SET）：正例 **Y** → 砍量 **N**
- 砍量禁 SET 控制：**Y**（`set_upgrade.log_absent`）
- 数据量仍小（辅）：**Y**（naive `1611753536` B / e3 `1615633664` B）
- torch_trace 升详缺失：**Y**（naive rows=`0` vs e3=`54054`）
- P3 RSS 主证单独：正例 **Y**（`rise_kb=268260:max_kb=2489768:n=200`）· 砍量 **Y**（`rise_kb=461300:max_kb=2519612:n=200`）

## 分臂对照

| 臂 | 配置 | total_dump_B | MiB | cold_B | RSS | SET↑ | path_enough | TT rows |
|----|------|-------------:|----:|-------:|:---:|:----:|:-----------:|--------:|
| E3 动态（正例） | rate=0→SET1.0 | 1615633664 | 1540.79 | 13390080 | Y | SET_OK | Y | 54054 |
| E4 砍量（naive） | rate=0 **禁SET** | 1611753536 | 1537.09 | 9509952 | Y | set_upgrade.log_absent | N | 0 |

### 砍量臂分表（top）

| table | bytes | MiB |
|-------|------:|----:|
| `python.torch_trace` | 320020480 | 305.20 |
| `python.comm_collective` | 320018432 | 305.19 |
| `python.torch_step_timing` | 320016384 | 305.19 |
| `python.trace_event` | 320013312 | 305.19 |
| `python.variables` | 320006144 | 305.18 |
| `cold` | 9509952 | 9.07 |
| `cpu.utilization` | 545792 | 0.52 |
| `gpu.utilization` | 545792 | 0.52 |
| `gpu.hccs` | 541696 | 0.52 |
| `cpu.tasks` | 535552 | 0.51 |

## 解读

- **掉级定义**：完整动态路径 = `RSS ∧ SET↑`（与 E2 `enough` 对齐）。砍量臂设计去掉触发升详 → path_enough=N，相对 E3 正例掉级。
- P3-SW 周期 `cpu.utilization` RSS 不依赖 torch 常驻密度；若砍量臂 RSS 仍 Y，说明「只砍 torch rate」对 **周期小表主证** 不够致命，但 **升详半截缺失** 仍证机制不可省（否则只剩粗判、无 W* 详采窗）。
- 这挡「你不就是把采样率调低了吗」：同常驻稀度下，有无触发升详决定能否走完动态路径。
- **禁止**用训练 step_ms 并比；全量臂本轮未重跑。

## 设计回哺

- 省量必须配触发升详：E4 砍量臂缺 SET↑ → 完整路径归因掉级；E3 有 SET↑ → 同 RSS 覆盖下总量 72.6%。
- 常驻 rate=`0` + SAMPLE_MS=500 可保周期小表；**不可**省略 mid SET 升详。

## 产物

- `E4_ABLATION.json` · `naive_cut/` · `e3_positive/REUSE.txt`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v2/20260726_182630-pillar-c-e4-p3-sw-a-loud`
- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/20260726_182630-pillar-c-e4-p3-sw-a-loud/`
