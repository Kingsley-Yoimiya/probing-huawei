# 32 卡自然 Stall：长窗复测与参数选择

日期：2026-08-07
环境：`grj-megatron-32card-0716`（2×16 Ascend）
共同设置：无人工注入；`compute -> all_reduce -> npu.synchronize`；host timeline
全量测时；`rotate k=4`（每 step 采 4/32 ranks 的 compute + collective NPU
Events）；`host_iter_ms >= 200` 触发；异常落盘 + 10k-step heartbeat。

## 结论

在本轮测试的参数中，**`matmul_size=4096`、`all_reduce=1 MiB` 是后续大规模自然
stall 复现的首选配置**：两次独立实验的点估计分别为 56.91 和 53.15 incidents/M
sync（2.79 和 2.69/min），差 6.6%；严重度 p50 分别为 685.0 和 685.7ms，critical-path
占比分别为 2.94% 和 2.85%。两轮合并得到 41 incidents / 754k sync / 902.0s，
即 54.38/M sync 或 2.73/min。

但它目前是“最合适的复现配置”，**不是已证明恒定的全局 stall 概率**：

1. 两次 4096 轮次的 rate ratio 为 0.934，95% CI 为 `[0.490, 1.781]`。点估计很接近，
   但只有 41 个事件，区间还未完整落入预注册的 `[2/3, 1.5]` 等价范围；
2. 10min 轮次前后各半得到 21 vs 6 incidents。等 exposure、恒定 rate 的探索性
   conditional binomial test 为 `p=0.0059`，提示 session 内也可能衰减或成簇；
3. 完全相同的 1024 配置做两个 30min 长窗，rate 从 56.75 降到 23.90/M sync，
   rate ratio 0.421（95% CI `[0.297, 0.596]`），已经排除“只用一个固定 Bernoulli p”
   描述所有 session 的做法。

因此大规模实验应报告**每轮/每时间块的条件概率分布**，并把 run、node、候选 rank
和 run age 作为分层变量；不能只汇总一个跨轮次平均数。

## 参数比较

| 配置 / run | exposure | incidents | /M sync（95% CI） | /min | p50 | 650–750ms | critical path |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1024 + 1MiB，R1 30m | 1.815M / 1804.5s | 103 | 56.75（46.80–68.82） | 3.42 | 691.8ms | 85 | 3.71% |
| 1024 + 1MiB，R2 30m | 1.925M / 1801.6s | 46 | 23.90（17.92–31.87） | 1.53 | 687.3ms | 34 | 1.65% |
| 1024 + 16MiB，5m | 242k / 301.7s | 2 | 8.26（2.27–30.14） | 0.40 | 702.5ms | 2 | 0.47% |
| 4096 + 1MiB，R1 5m | 246k / 300.8s | 14 | 56.91（33.90–95.53） | 2.79 | 685.0ms | 10 | 2.94% |
| 4096 + 1MiB，R2 10m | 508k / 601.2s | 27 | 53.15（36.53–77.33） | 2.69 | 685.7ms | 21 | 2.85% |

`16MiB` 通信 arm 只有 2 个事件，复现密度明显不足，且置信区间过宽，不选。4096 arm
同时保持较高的复现密度、近乎一致的严重度和较接近的独立轮次点估计，因而胜出。

## 阈值选择

建议保留双指标：

- **宽口径告警**：`host_iter_ms >= 200`，用于捕获全部异常支峰；
- **预注册主事件**：incident severity `>= 650ms`，用于比较稳定的约 700ms stall 模式。

R1 30min 的离线阈值扫描为：200/300/500/650ms 分别得到
103/98/91/85 incidents，说明主模式对 200–650ms 的阈值变化不敏感。两次 4096 轮次
合并后，31/41（75.6%）属于 650–750ms，主事件率为 41.11/M sync（2.06/min）。

## 对 time-driven / sync-driven 的判断

本轮仍不能唯一识别机制。1024 两个 30min session 的巨大差异说明 session/node state
是强混杂；4096 的 10min 轮次又出现 run-age 衰减，而单一 workload 内 wall time 与 sync
count 几乎共线。

下一步应在**同一进程、不重启 rank**的条件下做随机化 `A-B-B-A` block：

- A：1024 matmul + 1MiB AllReduce（快 cadence）；
- B：4096 matmul + 1MiB AllReduce（慢 cadence）；
- 每个 block 同时记录 `T`（wall seconds）与 `S`（sync count）；
- 用 `E[N] = lambda_time * T + lambda_sync * S` 拟合，并加入 run/block/node 随机效应。

同一 session 内切换 cadence 可以减少“这轮恰好某个 rank 活跃”的混杂；交替顺序可以把
参数效应与随训练时间衰减分开。

## 大规模测试建议配置

首轮扩展建议固定：

```text
matmul_size=4096
all_reduce_bytes=1048576
scheme=rotate
sample_ranks=4            # 32 卡时覆盖 12.5%；更大规模按固定预算或固定比例另行声明
record_mode=anomaly
heartbeat_every=10000
host_trigger_ms=200
primary_severity_ms=650
inject_kind=none
```

统计与 gate：

1. 每个独立 session 至少累计 50 个 incident，再判断 rate 稳定性；按当前 2.7/min 点估计，
   约需 20min，但必须以实际计数为准；
2. 同时报 `/min`、`/M sync`、650ms 主事件率、severity 分位数和 critical-path wall fraction；
3. 固定 60s block 报告零事件比例、Fano factor 和前/后半段 rate，检查过度离散和衰减；
4. 用分层 Poisson/negative-binomial，而不是把所有 rank-step 当独立 Bernoulli；
5. host 等待按 incident critical-path union 去重，禁止把 32 ranks 的传播等待相加。

## Probing 数据链与规模边界

五轮正式数据的 Probing/raw exact key round-trip 均为 32/32 ranks；4096 R2 为
2496/2496 rows。host timeline 能稳定捕获 stall，rotate events 提供 12.5% device phase
探索覆盖。

本轮还暴露了规模化存储问题：4096 R2 的可复盘紧凑证据约 2.7MiB，但固定版 Probing
为内建表和 32 个自定义表预分配的 `probing_data` 约 5.7GiB。远端完整 mmap/ring 保留，
本地只回拉 summary、analysis、logs、meta、raw/probing JSONL。扩到更多卡前应切换为只匹配
训练 rank 的 Probing 启动方式、关闭未使用内建 collector，并验证最小 ring 容量；否则固定
预分配会成为存储瓶颈，而不是事件行数本身。

## 证据路径

- 本地紧凑证据：
  `myportal/results/huawei-a3-32/stall-timeline/<run_id>/`
- 远端完整 Probing ring：
  `/afs-a3-weight-share/yinjinrun.p-huawei/results/stall-timeline/<run_id>/`
- 关键 run：
  - `20260807_024752-stall-timeline-32-natural-r1-30m-v1`
  - `20260807_033022-stall-timeline-32-natural-r2-30m-v1`
  - `20260807_040228-stall-timeline-32-param-comm16m-5m`
  - `20260807_040852-stall-timeline-32-param-mm4096-5m`
  - `20260807_041544-stall-timeline-32-param-mm4096-r2-10m`
