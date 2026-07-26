# ②-A · 追溯窗 W*（Param-Calib）

> **参数**：torch_trace 追溯窗 W* = 首次够归因的最小 W。
> **尺**：采集内容够不够归因；**禁止**训练 step_ms / cold 冒充；**不**把 v2 E1-off 当默认。
> **自变量**：W∈{10,25,50,100,200,全程}；锚 inject_stop=300；victim=7；窗[100,300]。

## 方法

1. 读 victim `python.torch_trace` MEMT（+ P3 的 `cpu.utilization` RSS）。
2. 锚在 `inject_stop=300`，截 W 步重判。
3. case 主证：
   - **P3-SW-A/B**：`cpu.utilization` RSS 窗内抬升 ≥50MiB（须与注入窗时间对齐）。
   - **P1-HW-B**：优先 alloc ramp；若平坦则退回注入窗内 duration 尖刺（≥3×中位且≥0.4s）。
   - **P1-SW-C**：post-forward duration 尖刺（≥3×中位且≥0.4s）。
4. **W*** = 首次 enough=true 的最小 W。

## 选定值

- **设计默认 W\*** = **100**（规则：max(W*_case) over OK cases (conservative cover)）
- 逐 case：P1-HW-B=100, P1-SW-C=100

## W* 总表

| case | W* | status | parent | primary |
|---|---:|---|---|---|
| P1-HW-B | 100 | OK | `20260726_001353-pillar-c-p1-hw-b-loud` | torch_trace.duration_spike(hbm_pressure) |
| P1-SW-C | 100 | OK | `20260726_012627-pillar-c-p1-sw-c-loud` | torch_trace.duration_spike |

## 分窗明细

### P1-HW-B

- parent：`20260726_001353-pillar-c-p1-hw-b-loud`
- torch_trace：`/Users/yinjinrun/Codespace/myportal/results/ascend-ais/_prep/param_calib_2a/offline_mirror/20260726_001353-pillar-c-p1-hw-b-loud/full_fidelity/probing_data/596522/python.torch_trace` (pid=596522)
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `100` status=`OK`

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
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `100` status=`OK`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `no_spike:top_step=296:dur_s=0.2056:med=0.1993:n_steps=10` |
| 25 | N | 25 | `no_spike:top_step=286:dur_s=0.2093:med=0.1991:n_steps=25` |
| 50 | N | 50 | `no_spike:top_step=286:dur_s=0.2093:med=0.1994:n_steps=50` |
| 100 | Y | 100 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.1994:module=AdamW` |
| 200 | Y | 200 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.2017:module=AdamW` |
| full | Y | 300 | `torch_trace.duration_spike:step=238:dur_s=0.7115:med=0.2004:module=AdamW` |

## 这数据证明为什么这么设

对已 OK 的 case，W* 分别为 P1-HW-B=100, P1-SW-C=100。设计默认取 **max(W*)=100**：保证最苛刻 case 仍够归因；更短窗会在至少一个 case 上丢掉尖刺/RSS 抬升证据。

## 诚实

- 本队列自跑曲线；v2 E1-off W*=100 **不作默认**（对照可写，不直接采用）。
- P3 旧 full_fidelity 若 `cpu.utilization` 环未覆盖注入窗 → UNRESOLVED，须 P-FIX 后新跑。
- 禁止用 cold MiB / 训练 step_ms 冒充「够归因」。

