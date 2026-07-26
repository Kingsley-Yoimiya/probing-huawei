# E1-off · 够用最小窗 W*（离线截窗初版）

> **定位**：EVAL-GAP §2 E1 / §5 离线近似。不占集群卡。
> **尺**：判「采集内容够不够归因」；**禁止**用训练 step_ms 把各窗判成同 D。
> **数据**：旧 Pillar-C `full_fidelity` 的 `probing_data/<pid>/python.torch_trace`（MEMT）。

## 方法

1. 读 victim PID 的 `python.torch_trace` MEMT（环 20MB；`rows_overwritten` 记入表）。
2. **锚在注入窗结束** `inject_stop=300`（非 dump 末步），截 `W∈{10,25,50,100,200,全程≤anchor}`。
3. 按 case 判「够归因」：
   - **P3-SW-A/B**：主证 `cpu.utilization` RSS 抬升（≥50MiB 或绝对值 ≥700MiB；按 torch 时间窗切 RSS）；辅看按步 `allocated`。
   - **P1-HW-B**：`torch_trace.max_allocated` 窗内抬升/斜率（≥256MiB 级）。
   - **P1-SW-C**：`torch_trace` post-forward `duration` 尖刺（≥3×中位且 ≥0.4s）。
4. **W*** = 首次 enough=true 的最小 W；全程仍不够 → `NO_W_STAR`（不拿 cold MiB 冒充）。

## W* 总表

| case | W* | status | anchor | steps in ring | rows_ow | primary evidence |
|---|---:|---|---:|---:|---:|---|
| P3-SW-A | — | UNRESOLVED | 300 | 546 (0..545) | 0 | cpu.utilization_rss |
| P3-SW-B | — | UNRESOLVED | 300 | 546 (0..545) | 0 | cpu.utilization_rss |
| P1-HW-B | — | NO_W_STAR | 300 | 546 (0..545) | 0 | torch_trace.max_allocated_ramp |
| P1-SW-C | 100 | OK | 300 | 546 (0..545) | 0 | torch_trace.duration_spike |

## 分窗明细

### P3-SW-A

- parent：`20260725_230350-pillar-c-p3-sw-a-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity/probing_data/240708/python.torch_trace` (pid=240708)
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `None`
- ⚠ UNRESOLVED：`rss_ts=[1784992174535243,1784992175542712] vs torch_inject_ts=[1784991972206201,1784992081889358] (no overlap)`
- C2 dump 旁证（不定 W*）：rss_rise_kb=171736

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `rss_ring_misaligned_to_inject_window` |
| 25 | N | 25 | `rss_ring_misaligned_to_inject_window` |
| 50 | N | 50 | `rss_ring_misaligned_to_inject_window` |
| 100 | N | 100 | `rss_ring_misaligned_to_inject_window` |
| 200 | N | 200 | `rss_ring_misaligned_to_inject_window` |
| full | N | 301 | `rss_ring_misaligned_to_inject_window` |

### P3-SW-B

- parent：`20260725_233537-pillar-c-p3-sw-b-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260725_233537-pillar-c-p3-sw-b-loud/full_fidelity/probing_data/406190/python.torch_trace` (pid=406190)
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `None`
- ⚠ UNRESOLVED：`rss_ts=[1784994125285910,1784994126295790] vs torch_inject_ts=[1784993950241139,1784994039767285] (no overlap)`
- C2 dump 旁证（不定 W*）：rss_rise_kb=13964

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `rss_ring_misaligned_to_inject_window` |
| 25 | N | 25 | `rss_ring_misaligned_to_inject_window` |
| 50 | N | 50 | `rss_ring_misaligned_to_inject_window` |
| 100 | N | 100 | `rss_ring_misaligned_to_inject_window` |
| 200 | N | 200 | `rss_ring_misaligned_to_inject_window` |
| full | N | 301 | `rss_ring_misaligned_to_inject_window` |

### P1-HW-B

- parent：`20260726_001353-pillar-c-p1-hw-b-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260726_001353-pillar-c-p1-hw-b-loud/full_fidelity/probing_data/596522/python.torch_trace` (pid=596522)
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
- 截窗锚：`anchor_step=300` (inject [100,300])
- **W\*** = `None`
- ⚠ 全程窗仍不够归因（见分窗 evidence）；**未**用 cold MiB 冒充。

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 10 | N | 10 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=291..300` |
| 25 | N | 25 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=276..300` |
| 50 | N | 50 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=251..300` |
| 100 | N | 100 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=201..300` |
| 200 | N | 200 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=101..300` |
| full | N | 201 | `torch_trace_blind_in_inject:rise_mb=0.0:slope_mb=0.0:steps=100..300` |

### P1-SW-C

- parent：`20260726_012627-pillar-c-p1-sw-c-loud`
- torch_trace：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260726_012627-pillar-c-p1-sw-c-loud/full_fidelity/probing_data/1174526/python.torch_trace` (pid=1174526)
- 环内：rows=79353 steps=546 recycled=0 overwritten=0
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

## 缺口 / 局限

- C2 `query_torch_trace_tail` 曾因 SQL 列名 `step`（应为 `local_step`）失败，故本轮直接解析 MEMT，不依赖错误 dump。
- 截窗锚在 `inject_stop=300`（Loud 注入窗），不是 dump 末步；避免「末窗假短」。
- **P3-SW**：`cpu.utilization` 热环太小，落盘后常只剩 run 末尾 ~1s，与注入窗时间错位 → `UNRESOLVED`（C2 dump 可证 RSS 抬升存在，但不能离线定 W*）。
- **P1-HW-B**：注入窗内 `allocated/cached/max_allocated` 平坦；禁止把 inject 前冷启动 +1024MB 当 ramp → `NO_W_STAR`。
- P1-EXT-A 阴性对照本轮未跑（可后补）。
- 若 `rows_overwritten>0`，环已丢早期步，「全程」≠训练全程。
- 正式 E1 仍需按 W 保留的新采集；本表是离线初版。

## 复跑

```bash
python3 project/probing-huawei/scripts/fail-slow/e1_offline_window_score.py \
  --out project/probing-huawei/results/ascend-ais/pillar_c_v2/E1_off \
  --afs-root /afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c
```

