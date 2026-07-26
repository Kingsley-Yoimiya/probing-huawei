# E2_RATE · 平时多稀够不够触发

> case=`P3-SW-A` loud · parent=`20260726_173134-pillar-c-e2-p3-sw-a-loud` @ grj-w0  
> **尺**：采集内容（`cpu.utilization_rss`）够不够粗判/归因；量用**总落盘**；**禁止**只用 cold；**禁止**训练 step_ms 判同 D。  
> 流程：常驻 `rate=R` + 周期小表 500ms → 注入 onset（L≥100）附近 SET `on,rate=1.0` → dump。

## 结论：够触发的最稀常驻率 = **`0`**

边界两档（`0` / `0.05`）均 **trigger_ok**；中间 `0.001`/`0.01` **不必再扫**（最稀边界已是 0）。

| rate | trigger_ok | RSS 主证 | SET↑ | total_dump_B | cold_B | 备注 |
|------|------------|----------|------|--------------|--------|------|
| **0** | **Y** | rise≈308MB max≈2.53GB n=200 | FAIL（PATH） | 1 611 932 672 | 9 689 088 | 周期小表已够粗判 |
| 0.05 | Y | rise≈390MB max≈2.47GB n=200 | FAIL（PATH） | 1 611 985 600 | 9 742 016 | 与 rate=0 同量级 |

- 主证路径：`probing/query_p3sw_rss_window.txt`（`cpu.utilization` process scope）。
- 金标覆盖复用 B Loud D4；**未**用训练 step_ms 判臂。

## 设计回哺

1. **常驻 torch rate 默认可取 `0`**（至少对 P3-SW host 泄漏类）：触发靠周期 `cpu`/`gpu` 小表 + step_timing，不依赖 torch_trace 常驻密度。
2. 印证摸底：「检测触发靠周期小表，torch_trace 平时可不写」。
3. **SET↑ 本轮两臂均 FAIL**：`jexec` 内 `PATH` 缺 `/usr/bin` → `date`/`ps`/`awk` not found → `CANDS=`/`SET_FAIL_ALL`。机制本身 C0-a 已 PASS；`hold_exec_run_case.sh` 已补 `PATH=/usr/bin:/bin:...`（供 E3）。**勿把 SET_FAIL 解读成 rate=0 盲区。**

## 路径纪律（本轮踩坑）

| 项 | 处置 |
|----|------|
| `172752` INVALID | `POD_RESULTS` 被 `env.sh` 早默认写成 `/data/...`（grj 无此盘） |
| `env.sh` | 已去掉 `/data` 早默认；AFS 为默认；本轮命令行仍强制 `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais` |
| `POD_BUNDLE` | `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle` |
| 产物 | `pillar_c_v2/20260726_173134-pillar-c-e2-p3-sw-a-loud/`（非旧 `pillar_c/`） |

## 未做 / 留给后续

- 中间 rate `0.001`/`0.01`：边界已定，跳过。
- SET↑ 热更后 torch_trace 密度差：等 PATH 修复后在 E3 动态臂验证（C0-a 已证机制）。
- rate_0.05 本机 `hold_exec.rc` 曾因并发改脚本出现 quote EOF（rc=2）；**远端 jsonl=16 + dump 已齐**，判分有效。

## 产物

- `E2_RATE.json` · `rate_{0,0.05}/` · AFS 同路径  
- 本机：`results/ascend-ais/pillar_c_v2/20260726_173134-pillar-c-e2-p3-sw-a-loud/`
