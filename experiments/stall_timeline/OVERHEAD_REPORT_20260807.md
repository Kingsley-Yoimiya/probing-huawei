# 32 卡 Stall Observer 训练开销实验

日期：2026-08-07
环境：`grj-megatron-32card-0716`（2×16 Ascend）
workload：4096 matmul + 1MiB AllReduce + workload 原有 `npu.synchronize()`
注入：无

## 测量设计

为避免自然 stall rate、run age 和 session state 被误算成 observer overhead，在同一
torchrun 进程内交替执行 control 与 treatment block。每个正式 block 固定 30,000 sync，
每个 treatment 前后都由 control 包夹：

```text
control -> treatment -> control
```

treatment throughput 除以前后两个 control throughput 的几何均值。每种 treatment 放在
不同 run age 位置重复3次。所有模式都保留同一个最外层 iteration 计时器，因此报告的是
相对 control 的增量开销。

模式定义：

- `control`：原 workload + 两次外层 CPU timestamp；
- `host`：增加 compute / AllReduce call / synchronize 分段 host timestamp；
- `rotate`：host + 每 step 轮换4/32 ranks，每个 sampled rank 记录3个 NPU Events；
- `full`：rotate + 异常/heartbeat JSONL 与 `python.stall_timeline`；
- `*_pool`：复用 NPU Event 对象；
- `host_full`：host + 异常/heartbeat 落盘，不记录 device Event；
- `full_sparse1_pool` / `full_sparse5_pool`：host_full + 仅1%/5%全局 step 做
  rotate-k4 device Event。

`raw throughput` 包含自然 stall；`stall-adjusted throughput` 按原有规则把相邻异常 step
合并为 incident，并从 block elapsed 中扣除 incident 最大 critical-path duration。由于
阈值以下的尾部仍会影响 wall time，同时报告 clean iteration 分位数和均值。

## 当前实现开销

run：`20260807_054000-stall-observer-overhead-ab-32-r1`，19 blocks / 570k sync。

| 模式 | 重复 | raw throughput | stall-adjusted | clean p50 | clean mean |
|---|---:|---:|---:|---:|---:|
| host | 3 | -0.51% | -0.51% | +0.27% | -0.44% |
| rotate-k4 every step | 3 | +10.72% | +10.08% | +11.62% | +10.67% |
| full | 3 | +9.60% | +10.10% | +11.06% | +9.28% |

host timestamp 本身低于当前噪声分辨率。约10%的主要成本来自 device Event 热路径，而
不是异常落盘。

## Event pool 消融

run：`20260807_061500-stall-observer-pool-ab-32-r1`，13 blocks / 390k sync。

| 模式 | 重复 | raw throughput | stall-adjusted | clean p50 | clean mean |
|---|---:|---:|---:|---:|---:|
| rotate pool | 3 | +6.26% | +10.04% | +11.79% | +10.43% |
| full pool | 3 | +10.41% | +11.13% | +11.71% | +10.57% |

复用对象没有降低 clean-step 成本，因此瓶颈是 Event record / elapsed 及其对全局同步
critical path 的影响，而不是 Python Event allocator。不能把 event pool 当成解决方案。

## 推荐稀疏方案

run：`20260807_065000-stall-observer-sparse-ab-32-r1`，19 blocks / 570k sync；关闭本实验
不需要的 Probing CPU/GPU/HCCS、span 和 cluster heartbeat collector。

| 模式 | 重复 | raw throughput | stall-adjusted | clean p50 | clean mean | clean p90 |
|---|---:|---:|---:|---:|---:|---:|
| host_full | 3 | +0.55% | +1.65% | +0.44% | +0.92% | +1.29% |
| host + 1% device steps | 3 | +1.46% | +0.74% | +0.27% | -0.07% | +3.84% |
| host + 5% device steps | 3 | +9.36% | +2.82% | +0.16% | +2.17% | +11.37% |

单轮只有3个 treatment block，raw throughput range 仍受自然 stall 不均匀分配影响：

- host_full raw range：`-1.60%..+5.60%`；
- 1% sparse raw range：`-0.23%..+6.35%`；
- 5% sparse raw range：`-5.27%..+13.27%`。

所以不能声称 host/1% 的严格95%上界已经低于1%；当前更稳妥的描述是：clean p50
增量低于0.5%，stall-adjusted 点估计约0.7%～1.7%，而5%探索已经在 mean/p90 和
stall-adjusted throughput 上产生明显成本。

## 常驻配置决策

### 只估计 stall 发生概率与训练损失

使用 `host_full`：100% step host timeline，异常≥200ms + 每10k step heartbeat。自然 stall
检测不依赖 device sampling；此前的约700ms分布、发生率和 critical-path wall fraction
都可以从 host timeline 得到。

### 同时保留 device-origin 无偏探索

使用 `host_full + 1% global steps rotate-k4 pooled Events`。这不是每 rank 1%：在一个
被选择的全局 step 上仍只采4/32 ranks。任一单次 stall 的 source rank 当步被命中概率约为
`1% * 4/32 = 0.125%`，因此它只适合长时间无偏探索，不保证定位当前一次 one-off stall。

实时定位应采用：host shape 给 candidate → 未来100～500 step 对 candidate/ring 邻居短窗
升采样；若必须回溯当前事件，则需要低成本硬件 flight recorder / CANN ring，而不是把
NPU Events 每 step 常驻。

### 禁止项

- 不使用 every-step rotate-k4：约10%训练损失；
- 不使用5% device-step 作为默认：尾部和平均开销已明显；
- 不为观测额外插入 `npu.synchronize()`：本实验复用 workload 原有同步点；
- 不把同步传播后的32个 rank 等待相加，训练损失按 incident critical path 去重。

## Probing 常驻与存储边界

跨进程 `off -> default on -> off` 配对显示 default-on 的 clean p50 点估计仅+0.31%，但
clean mean +3.09%、p99 +8.84%；完整吞吐点估计-1.84%且区间跨零。另一个 minimal-on
轮次反而显著快于两个 off，说明进程重启后的系统状态仍强烈混杂，因此不能把这些跨进程
点估计作为严格因果 overhead。可靠决策以上述同进程邻接 control 为准。

minimal 配置把远端预分配从此前约5.7GiB降到约3.9GiB，但仍存在32个自定义表以及额外
Python trace/collective mmap。大规模部署前还应使用 script-filtered activation，只注入训练
rank，并继续压缩固定 ring；这主要是存储问题，不等同于训练热路径流量。

推荐稀疏正式 run 的 raw/Probing 回读为1534/1534 rows、32/32 ranks 精确一致。

## 证据

- 当前 observer：
  `myportal/results/huawei-a3-32/stall-timeline/20260807_054000-stall-observer-overhead-ab-32-r1/`
- Event pool：
  `myportal/results/huawei-a3-32/stall-timeline/20260807_061500-stall-observer-pool-ab-32-r1/`
- 推荐 sparse：
  `myportal/results/huawei-a3-32/stall-timeline/20260807_065000-stall-observer-sparse-ab-32-r1/`
- 远端完整 Probing mmap：
  `/afs-a3-weight-share/yinjinrun.p-huawei/results/stall-timeline/<run_id>/`
