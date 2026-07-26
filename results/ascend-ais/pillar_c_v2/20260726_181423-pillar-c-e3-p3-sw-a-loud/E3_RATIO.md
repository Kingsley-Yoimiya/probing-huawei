# E3_RATIO · 同覆盖下总落盘比（头条）

> case=`P3-SW-A` loud · parent=`20260726_181423-pillar-c-e3-p3-sw-a-loud` @ grj-w0
> 常驻 rate=`0`（E2 `173134`）· 设计窗 W\*=`100`（E1-off；正式 E1 `173830` NO_W_STAR **不推翻**）
> **主尺**=总落盘字节（全表）动态/全量；**禁止**只报 cold；**禁止**训练 step_ms 并比。
> 判分=采集内容够归因（P3-SW：`cpu.utilization_rss`）；覆盖复用 B Loud D4。

## 结论：动态/全量 = **72.6%**（W\*=100 content est）

- 同覆盖（RSS 够归因）：**Y**（`rise_kb=268260:max_kb=2489768:n=200`）
- SET↑：`SET_OK` latency=931ms；键=`probing.torch.profiling=on,rate=1.0`（读回 `on,rate=1.0`）
- **raw** 总落盘比：`90.16%`（动态 `1615633664` / 全量 `1791975360`）
- **W\* content est** 比：`72.6%`（动态估 `1300989872` / 全量 `1791975360`）
- window_mode=`offline_truncate_estimate`（无 online retention API）

## 分臂字节表

| 臂 | 配置 | total_dump_B | MiB | cold_B | cold MiB | RSS | SET↑ |
|----|------|-------------:|----:|-------:|---------:|:---:|:----:|
| 动态 raw | rate=0→SET1.0 SAMPLE_MS=500 | 1615633664 | 1540.79 | 13390080 | 12.77 | Y | SET_OK |
| 动态 W\*估 | TT 截窗+空 rank 不计内容 | 1300989872 | 1240.72 | 13390080 | 12.77 | Y | SET_OK |
| 全量（复用） | rate=1.0 SAMPLE_MS=50 | 1791975360 | 1708.96 | 169366016 | 161.52 | Y* | n/a |

\*全量臂覆盖复用 B Loud D4，不重判训练。

### 分表对照（动态 raw vs 全量）

| table | 动态 B | 全量 B | 动态/全量 |
|-------|-------:|-------:|----------:|
| `python.torch_trace` | 320020480 | 320020480 | 100.0% |
| `python.comm_collective` | 320018432 | 320018432 | 100.0% |
| `python.torch_step_timing` | 320016384 | 320016384 | 100.0% |
| `python.trace_event` | 320013312 | 320013312 | 100.0% |
| `python.variables` | 320006144 | 320006144 | 100.0% |
| `cold` | 13390080 | 169366016 | 7.9% |
| `gpu.utilization` | 545792 | 545792 | 100.0% |
| `cpu.utilization` | 545792 | 545792 | 100.0% |
| `gpu.hccs` | 541696 | 541696 | 100.0% |
| `cpu.tasks` | 535552 | 535552 | 100.0% |

## W\* 截窗说明

- 锚 `inject_stop=300`，保留 `(300-100, 300]` 步的 torch_trace **内容量**近似。
- torch_trace：raw=`320020480` → W est=`5376688`（dense ranks=`1`/16）。
- **SET 脚本在首个 ATTACH_OK worker 后 `break`**：本轮仅 pid=`1855451` 升详（54054 rows / 372 steps）；其余 15 份 TT 环预分配但空内容，W\* 估按 0 计。
- P3-SW 主证在周期 `cpu.utilization` RSS，不依赖全 rank torch_trace；空 rank TT 不影响本 case 归因尺。

## 设计回哺

- 头条：**同归因下 动态/全量 ≈ 72.6%**（W\* content est）；on-disk raw ≈ 90.16%。
- 省量主要来自：① SAMPLE_MS 500 vs 50 → cold 12.8 vs 161.5 MiB；② torch 常驻 rate=0 + 触发升详（本轮仅 1 rank 实写）；③ W\* 截窗进一步压详采内容。
- 固定容量环（~20MB×表×rank）使 raw 总落盘仍接近全量；真正「设计量」看 W\* est / cold。
- 全量臂只作上界；**step_ms 不并比**。

## 产物

- `E3_RATIO.json` · `rate_0/` · `full_fidelity/REUSE.txt`
- 本机：`results/ascend-ais/pillar_c_v2/20260726_181423-pillar-c-e3-p3-sw-a-loud/`
- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/20260726_181423-pillar-c-e3-p3-sw-a-loud/`
