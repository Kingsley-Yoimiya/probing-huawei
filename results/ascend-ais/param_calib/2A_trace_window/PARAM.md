# ②-A · 追溯窗 W*（Param-Calib）

> **参数**：torch_trace 追溯窗 W* = 首次够归因的最小 W。
> **尺**：采集内容够不够归因；**禁止**训练 step_ms / cold 冒充；**不**把 v2 E1-off 当默认。
> **自变量**：W∈{10,25,50,100,200,全程}；锚 inject_stop=300；victim=7；窗[100,300]。

## 选定值

- **设计默认 W\*** = **100**（规则：max(W*_case)）
- 逐 case：P1-HW-B=100, P1-SW-C=100, P3-SW-A=10, P3-SW-B=10

## W* 总表

| case | W* | status | parent | primary | note |
|---|---:|---|---|---|---|
| P1-HW-B | 100 | OK | `20260726_001353-pillar-c-p1-hw-b-loud` | torch_trace.duration_spike(hbm_pressure) | duration_spike fallback（alloc 平坦） |
| P1-SW-C | 100 | OK | `20260726_012627-pillar-c-p1-sw-c-loud` | torch_trace.duration_spike | duration_spike；自跑曲线 |
| P3-SW-A | 10 | OK | `20260727_004715-2a-p3-sw-a-loud` | cpu.utilization_rss | 004715 PARTIAL；RING=64 RSS 对齐 |
| P3-SW-B | 10 | OK | `20260727_010224-2a-p3-sw-b-loud` | cpu.utilization_rss | 010224 挂起后只读评分；未杀训 |

## 分窗明细

### P1-HW-B

- parent：`20260726_001353-pillar-c-p1-hw-b-loud`
- torch_trace：`/Users/yinjinrun/Codespace/myportal/results/ascend-ais/_prep/param_calib_2a/offline_mirror/20260726_001353-pillar-c-p1-hw-b-loud/full_fidelity/probing_data/596522/python.torch_trace` (pid=596522)
- 环内：rows=79353 steps=546 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `100`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=291..300;no_spike:top_step=291:dur_s=0.2206:med=0.2004:n_steps=10` |
| 25 | N | 25 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=276..300;no_spike:top_step=291:dur_s=0.2206:med=0.2006:n_steps=25` |
| 50 | N | 50 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=251..300;no_spike:top_step=291:dur_s=0.2206:med=0.1992:n_steps=50` |
| 100 | Y | 100 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=201..300;fallback=torch_trace.duration_spike:step=201:dur_s=0.8641:med=0.1984:module=DistributedDataParallel` |
| 200 | Y | 200 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=101..300;fallback=torch_trace.duration_spike:step=201:dur_s=0.8641:med=0.1989:module=DistributedDataParallel` |
| full | Y | 201 | `alloc_flat_in_inject:rise_mb=0.0:slope_mb=0.0:steps=100..300;fallback=torch_trace.duration_spike:step=201:dur_s=0.8641:med=0.1989:module=DistributedDataParallel` |

### P1-SW-C

- parent：`20260726_012627-pillar-c-p1-sw-c-loud`
- torch_trace：`/Users/yinjinrun/Codespace/myportal/results/ascend-ais/_prep/param_calib_2a/offline_mirror/20260726_012627-pillar-c-p1-sw-c-loud/full_fidelity/probing_data/1174526/python.torch_trace` (pid=1174526)
- 环内：rows=79353 steps=546 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `100`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `no_spike:top_step=296:dur_s=0.2056:med=0.1993:n_steps=10` |
| 25 | N | 25 | `no_spike:top_step=286:dur_s=0.2093:med=0.1991:n_steps=25` |
| 50 | N | 50 | `no_spike:top_step=286:dur_s=0.2093:med=0.1994:n_steps=50` |
| 100 | Y | 100 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.1994:module=AdamW` |
| 200 | Y | 200 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.2017:module=AdamW` |
| full | Y | 300 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.2004:module=AdamW` |

### P3-SW-A

- parent：`20260727_004715-2a-p3-sw-a-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/param_calib/2A_trace_window/20260727_004715-2a-p3-sw-a-loud/full_fidelity/probing_data/2057356/python.torch_trace` (pid=2057356)
- 环内：rows=35900 steps=248 overwritten=0
- 截窗锚：`anchor_step=247` (inject [100,300])
- RSS align：`True` note=`aligned_n=1638`
- **W\*** = `10`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | Y | 10 | `cpu.utilization_rss:rise_kb=240592:max_kb=2499616:n=123;torch_alloc_flat:step_rise_mb=-2268.3` |
| 25 | Y | 25 | `cpu.utilization_rss:rise_kb=241360:max_kb=2499616:n=321;torch_alloc_flat:step_rise_mb=-2268.3` |
| 50 | Y | 50 | `cpu.utilization_rss:rise_kb=241748:max_kb=2499656:n=645;torch_alloc_flat:step_rise_mb=-2268.3` |
| 100 | Y | 100 | `cpu.utilization_rss:rise_kb=245644:max_kb=2499656:n=1304;torch_alloc_flat:step_rise_mb=-2268.3` |
| 200 | Y | 200 | `cpu.utilization_rss:rise_kb=252832:max_kb=2499656:n=1638;torch_alloc_flat:step_rise_mb=-2268.3` |
| full | Y | 248 | `cpu.utilization_rss:rise_kb=252832:max_kb=2499656:n=1638;torch_alloc_flat:step_rise_mb=-2268.3` |

### P3-SW-B

- parent：`20260727_010224-2a-p3-sw-b-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/param_calib/2A_trace_window/20260727_010224-2a-p3-sw-b-loud/full_fidelity/probing_data/2119261/python.torch_trace` (pid=2119261)
- 环内：rows=39495 steps=273 overwritten=0
- 截窗锚：`anchor_step=272` (inject [100,300])
- RSS align：`True` note=`aligned_n=1709`
- **W\*** = `10`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | Y | 10 | `cpu.utilization_rss:rise_kb=164080:max_kb=4335088:n=118;torch_alloc_flat:step_rise_mb=-2268.3` |
| 25 | Y | 25 | `cpu.utilization_rss:rise_kb=426432:max_kb=4335088:n=280;torch_alloc_flat:step_rise_mb=-2268.3` |
| 50 | Y | 50 | `cpu.utilization_rss:rise_kb=854092:max_kb=4335212:n=534;torch_alloc_flat:step_rise_mb=-2268.3` |
| 100 | Y | 100 | `cpu.utilization_rss:rise_kb=1726636:max_kb=4338036:n=1027;torch_alloc_flat:step_rise_mb=-2268.3` |
| 200 | Y | 200 | `cpu.utilization_rss:rise_kb=2097788:max_kb=4338036:n=1643;torch_alloc_flat:step_rise_mb=-2268.3` |
| full | Y | 273 | `cpu.utilization_rss:rise_kb=2097788:max_kb=4338036:n=1663;torch_alloc_flat:step_rise_mb=-2268.3` |

## 这数据证明为什么这么设

对 4 case，W* 分别为 P1-HW-B=100, P1-SW-C=100, P3-SW-A=10, P3-SW-B=10。设计默认取 **max(W*)=100**：覆盖最苛刻的 tip/尖刺 case（P1）；P3 RSS 抬升在大环下 W*=10 即够，但若只用 10 会丢掉 P1 注入窗内的 duration 尖刺帧。

## 证据路径

- 终态：`results/ascend-ais/param_calib/2A_trace_window/{PARAM.json,PARAM.md}`
- P1 离线：`_partial_p1/` ← pillar_c full_fidelity MEMT
- P3-SW-A：`20260727_004715-2a-p3-sw-a-loud`（`_score_p3swa/`）
- P3-SW-B：`20260727_010224-2a-p3-sw-b-loud`（`_score_p3swb/`；训进程曾挂起，只读评分）
- `20260727_004540` → INVALID 半截
- 脚本：`project/probing-huawei/scripts/fail-slow/param_calib/2a_trace_window.py`

## 诚实

- 自跑曲线；v2 E1-off W*=100 **不作默认**（本轮 max 恰为 100，因 P1 尖刺需 W≥100）。
- P3 旧小环数据 UNRESOLVED；本轮 RING_MB=64 后 RSS 与注入窗对齐 → W*=10。
- P3 两跑均未完整到 node_0.done（004715 中断 / 010224 dump 后挂起）；MEMT 已覆盖注入段，足够定 W*。
- 禁止 cold / 训练 step_ms 冒充够归因。

