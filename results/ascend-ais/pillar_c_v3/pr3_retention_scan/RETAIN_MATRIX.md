# PR-3 追溯窗矩阵 (handbook §3.4)

> 追溯窗**不是**一个数字，是每种关键数据各一个。
> W* = 首次 enough=Y 的最小保留窗；all=全程仍不够 → NO_W_STAR。

## W* 总表

| case | 关键数据 | retain 单位 | W\* | 主证据 |
|---|---|---|---:|---|
| P1-SW-C | python.torch_trace duration | steps | 200步 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` |
| P3-SW-A | cpu.utilization RSS | secs | 60秒 | `cpu.utilization_rss:rise_kb=443928:max_kb=2684492:n=119:span_s=59.2` |
| P1-HW-B | gpu.utilization used_bytes | secs | 60秒 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |

## 结论

**追溯窗按关键数据分别是：**
- **P1-SW-C** (torch_trace duration): 200 步
- **P3-SW-A** (cpu.utilization RSS): 60 秒
- **P1-HW-B** (gpu.utilization used_bytes): 60 秒

## 分窗明细

### P1-SW-C

- status: **OK**
- anchor_step: `282`

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| 25 | N | 364 | `no_spike:top_step=261:dur_s=0.1996:med=0.1027:n_steps=4` |
| 50 | N | 546 | `no_spike:top_step=261:dur_s=0.1996:med=0.1027:n_steps=6` |
| 100 | N | 910 | `no_spike:top_step=261:dur_s=0.1996:med=0.1000:n_steps=10` |
| 200 | Y | 1820 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` |
| 500 | Y | 2731 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.0991:module=DistributedDataParallel` |
| all | Y | 2731 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.0991:module=DistributedDataParallel` |

### P3-SW-A

- status: **OK**
- anchor_ts_us: `1785243124274636`

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| 60s | Y | 119 | `cpu.utilization_rss:rise_kb=443928:max_kb=2684492:n=119:span_s=59.2` |
| 300s | Y | 220 | `cpu.utilization_rss:rise_kb=2542224:max_kb=2684492:n=220:span_s=109.9` |
| 900s | Y | 220 | `cpu.utilization_rss:rise_kb=2542224:max_kb=2684492:n=220:span_s=109.9` |
| 1800s | Y | 220 | `cpu.utilization_rss:rise_kb=2542224:max_kb=2684492:n=220:span_s=109.9` |
| 3600s | Y | 220 | `cpu.utilization_rss:rise_kb=2542224:max_kb=2684492:n=220:span_s=109.9` |
| all | Y | 220 | `cpu.utilization_rss:rise_kb=2542224:max_kb=2684492:n=220:span_s=109.9` |

### P1-HW-B

- status: **OK**
- anchor_ts_us: `1785257010644215`

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| 60s | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |
| 300s | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |
| 900s | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |
| 1800s | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |
| 3600s | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |
| all | Y | 214 | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |

