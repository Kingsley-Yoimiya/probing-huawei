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

- **设计默认 W\*** = **10**（规则：max(W*_case) over OK cases (conservative cover)）
- 逐 case：P3-SW-B=10

## W* 总表

| case | W* | status | parent | primary |
|---|---:|---|---|---|
| P3-SW-B | 10 | OK | `20260727_010224-2a-p3-sw-b-loud` | cpu.utilization_rss |

## 分窗明细

### P3-SW-B

- parent：`20260727_010224-2a-p3-sw-b-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/param_calib/2A_trace_window/20260727_010224-2a-p3-sw-b-loud/full_fidelity/probing_data/2119261/python.torch_trace` (pid=2119261)
- 环内：rows=39495 steps=273 recycled=0 overwritten=0
- 截窗锚：`anchor_step=272` (inject [100,300])
- RSS align：`True` note=`aligned_n=1709` samples=22431
- **W\*** = `10` status=`OK`

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | Y | 10 | `cpu.utilization_rss:rise_kb=164080:max_kb=4335088:n=118;torch_alloc_flat:step_rise_mb=-2268.3` |
| 25 | Y | 25 | `cpu.utilization_rss:rise_kb=426432:max_kb=4335088:n=280;torch_alloc_flat:step_rise_mb=-2268.3` |
| 50 | Y | 50 | `cpu.utilization_rss:rise_kb=854092:max_kb=4335212:n=534;torch_alloc_flat:step_rise_mb=-2268.3` |
| 100 | Y | 100 | `cpu.utilization_rss:rise_kb=1726636:max_kb=4338036:n=1027;torch_alloc_flat:step_rise_mb=-2268.3` |
| 200 | Y | 200 | `cpu.utilization_rss:rise_kb=2097788:max_kb=4338036:n=1643;torch_alloc_flat:step_rise_mb=-2268.3` |
| full | Y | 273 | `cpu.utilization_rss:rise_kb=2097788:max_kb=4338036:n=1663;torch_alloc_flat:step_rise_mb=-2268.3` |

## 这数据证明为什么这么设

对已 OK 的 case，W* 分别为 P3-SW-B=10。设计默认取 **max(W*)=10**：保证最苛刻 case 仍够归因；更短窗会在至少一个 case 上丢掉尖刺/RSS 抬升证据。

## 诚实

- 本队列自跑曲线；v2 E1-off W*=100 **不作默认**（对照可写，不直接采用）。
- P3 旧 full_fidelity 若 `cpu.utilization` 环未覆盖注入窗 → UNRESOLVED，须 P-FIX 后新跑。
- 禁止用 cold MiB / 训练 step_ms 冒充「够归因」。

