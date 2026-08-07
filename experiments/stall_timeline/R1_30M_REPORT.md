# 32 卡自然 Stall 分布 R1（30 min）

## 配置

- run：`20260807_024752-stall-timeline-32-natural-r1-30m-v1`
- world：32（GRJ 2×16）
- workload：`matmul -> all_reduce -> npu.synchronize`
- 注入：无
- observer：host timeline 全量测量；每 step 拓扑分散 rotate 4/32 ranks 的
  compute+collective NPU Events
- 落盘：仅异常、每 10k step heartbeat；raw JSONL + `python.stall_timeline`
- stall threshold：`host_iter_ms >= 200`
- incident：相邻异常 step 合并一次，rank 等待不重复计数

## 发生概率

| 指标 | R1 |
|---|---:|
| 实际 exposure | 1,815,000 sync / 1804.468s |
| host anomaly steps | 188 |
| 合并 incidents | 103 |
| 每百万 sync | 56.75 |
| 95% Wilson CI | 46.80–68.82 / M sync |
| 每分钟 | 3.42 |
| critical-path 阻塞和 | 66,891.7ms |
| 观测 wall time 占比 | 3.71% |

即本 workload 下点估计为：

```text
p(stall incident per sync) = 103 / 1,815,000 = 5.675e-5
平均约每 17,621 sync，或每 17.5s 出现一次 incident
```

这是固定合成 workload 的自然发生概率，不能直接外推成模型训练吞吐损失。

## 严重度分布

| 区间 | incident 数 | 比例 |
|---|---:|---:|
| 200–300ms | 5 | 4.9% |
| 300–500ms | 7 | 6.8% |
| 500–650ms | 6 | 5.8% |
| 650–750ms | 85 | 82.5% |

- p50：691.8ms
- p90：716.5ms
- p95：722.9ms
- max：734.2ms

因此 R1 的主分布不是平滑长尾，而是一个非常明显的约 700ms 模式，外加少量
200–650ms 支峰。

## 到达间隔

| 坐标 | p50 | p90 | range |
|---|---:|---:|---:|
| wall time | 14.08s | 33.87s | 1.57–67.59s |
| sync count | 13,779 | 35,281 | 804–70,927 |

单轮不能区分 time-driven 与 sync-driven；R2 必须保持相同 cadence 才能先做分布重复性，
之后再用不同 cadence arms 区分 exposure 机制。

## 定位形状

- anomaly shapes：85 `singleton`、86 `all_but_one`、16 `global`、1 `partial`；
- 86/103 incidents 可由 slow-set complement 给出 host candidate；
- rank 17：41 次；rank 16：33 次；二者合计占可定位 incident 的 86.0%；
- rank 16/17 都位于 worker 节点的 local rank 0/1。

这构成明显的节点/ring-position 假设，但还不是物理坏卡证明。需要 16-card
master/worker 单节点对照或交换 rank mapping。

## Probing 一致性

- 自定义表 SQL 回读：32/32 ranks；
- raw JSONL：9,090 rows；
- Probing SQL：9,090 rows；
- `(rank, step)` exact key round-trip：32/32 ranks，无缺失或多余事件。

因此 R1 已同时建立自然事件分布和 Probing 数据链一致性，不依赖人工注入。

## 当前证据边界与 R2 gate

R1 的 103 个 incident 已足够形成窄于早期短窗的发生率区间，但**单轮不能证明概率可重复**。
下一轮必须复用完全相同的代码、阈值、32 卡映射、sampling、heartbeat 和 30min exposure。

建议预注册 R2 gate：

1. R2 与 R1 rate ratio 的 95% CI 完整落入 `[2/3, 1.5]`；
2. R2 的 650–750ms 主峰占比及 p50/p90 与 R1 bootstrap 区间相容；
3. raw/Probing exact key round-trip 仍为 32/32；
4. 若 rank16/17 集中性不复现，将它作为 session-dependent localization，而不影响
   stall 发生概率的重复性结论。

按相同的 1,815,000-sync exposure 和 Poisson log-rate 近似，R2 若得到约 **92–118** 个
incident，则 rate-ratio 95% CI 可完整落入上述 `[2/3, 1.5]` 等价区间；最终仍以实际
exposure 计算，不直接按这个计数范围裁决。

## 证据路径

- 本地：
  `myportal/results/huawei-a3-32/stall-timeline/20260807_024752-stall-timeline-32-natural-r1-30m-v1/`
- 远端完整数据：
  `/afs-a3-weight-share/yinjinrun.p-huawei/results/stall-timeline/20260807_024752-stall-timeline-32-natural-r1-30m-v1/`
