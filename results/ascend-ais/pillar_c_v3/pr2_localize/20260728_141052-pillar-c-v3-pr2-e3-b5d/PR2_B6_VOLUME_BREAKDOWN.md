# PR-2 B6 · B5d 数据量拆账

> 时间：2026-07-28T14:40+08:00  
> parent：`20260728_141052-pillar-c-v3-pr2-e3-b5d`  
> 目标：解释 B5d `headline=115.05%` 为什么未达 `<100%`，决定 B6 下一步。

## 结论

B5d 的定位/SET 语义已成立，但数据量超标不是因为 `torch_trace` 的 W\* 截窗失败；PR-2 评分脚本已经把 15 个非 culprit rank 的空 `python.torch_trace` 按 0 计入 W\*。

真正主因是两类固定环成本仍被计入总落盘：

1. **main_empty 非 TT 环**：15 个非 culprit 主训练 rank 仍各自落 `python.comm_collective`、`python.torch_step_timing`、周期小表等，W\* 口径下合计 **1020.10 MiB**（约 full 的 **59.69%**）。
2. **extra_pid 环**：本轮存在 18 个额外 pid 的 probe data，合计 **864.10 MiB**（约 full 的 **50.56%**），主要是 `python.comm_collective` + CPU/GPU 周期表。

因此 B6 不应重复 B5d 原参长跑；优先修「rate=0 / 非 culprit / extra pid」的表启停或 dump 过滤策略，再短测。

## 头条数值

| 项 | bytes | MiB | ratio vs full |
|----|------:|----:|--------------:|
| full 参考 | 1,791,975,360 | 1708.96 | 100.00% |
| dynamic raw | 2,382,593,024 | 2272.22 | 132.96% |
| dynamic W\* est | 2,061,709,183 | 1966.20 | **115.05%** |
| raw `python.torch_trace` | 335,564,800 | 320.02 | 18.73% |
| W\* `python.torch_trace` est | 14,680,959 | 14.00 | 0.82% |

校验：`dynamic W* est = raw - raw_torch_trace + W*_torch_trace = 2,061,709,183`。

## W\* 口径下的主要组成

| 组件 | 类别 | bytes | MiB | ratio vs full |
|------|------|------:|----:|--------------:|
| `python.comm_collective` | extra_pid | 377,508,096 | 360.02 | 21.07% |
| `python.comm_collective` | main_empty | 314,590,080 | 300.02 | 17.56% |
| `python.torch_step_timing` | main_empty | 314,588,160 | 300.01 | 17.56% |
| `gpu.utilization` | extra_pid | 151,019,136 | 144.02 | 8.43% |
| `cpu.utilization` | extra_pid | 151,019,136 | 144.02 | 8.43% |
| `cpu.tasks` | extra_pid | 151,007,616 | 144.01 | 8.43% |
| `gpu.utilization` | main_empty | 125,849,280 | 120.02 | 7.02% |
| `cpu.utilization` | main_empty | 125,849,280 | 120.02 | 7.02% |
| `cpu.tasks` | main_empty | 125,839,680 | 120.01 | 7.02% |
| `gpu.hccs` | extra_pid | 75,517,056 | 72.02 | 4.21% |
| `gpu.hccs` | main_empty | 62,930,880 | 60.02 | 3.51% |
| `python.comm_collective` | main_dense | 20,972,672 | 20.00 | 1.17% |
| `python.torch_step_timing` | main_dense | 20,972,544 | 20.00 | 1.17% |
| `python.torch_trace` W\* est | main_dense | 14,680,959 | 14.00 | 0.82% |

## 按 pid 类别汇总

| 类别 | 含义 | bytes in W\*口径 | MiB | ratio vs full |
|------|------|-----------------:|----:|--------------:|
| main_empty | 15 个主训练非 culprit rank，非 `torch_trace` | 1,069,647,360 | 1020.10 | 59.69% |
| extra_pid | 不在 16 个主 rank 的额外 pid，非 `torch_trace` | 906,071,040 | 864.10 | 50.56% |
| main_dense | culprit rank 的非 TT + W\* TT | 85,990,783 | 82.01 | 4.80% |

注：这里的 W\* 口径已把所有 raw `python.torch_trace` 替换为 W\* 估算；`main_empty` 和 `extra_pid` 数字不再包含 raw TT。

## 反事实估算

| 假设 | 扣减 MiB | 估算 dynamic MiB | 估算 ratio |
|------|---------:|-----------------:|-----------:|
| 当前 B5d W\* | 0.00 | 1966.20 | **115.05%** |
| 去掉 extra_pid 非 TT | 864.10 | 1102.10 | **64.49%** |
| 去掉 main_empty 的 step+comm | 600.03 | 1366.17 | **79.94%** |
| 去掉 main_empty 的周期小表 | 420.06 | 1546.14 | **90.47%** |
| 去掉全部 main_empty 非 TT | 1020.10 | 946.10 | **55.36%** |

最小可行动作不必同时修所有表：只要让 main_empty 的 `python.torch_step_timing` + `python.comm_collective` 不落空环，估算就已降到 **79.94%**。

## B6 建议

优先做代码/收集策略修复，而不是调 `WINDOW_S`：

1. **非 culprit / rate=0 默认不启大环**：在未 SET 升详前，非 culprit rank 不应预分配 `python.comm_collective` 与 `python.torch_step_timing` 的 20MiB 环；如果诊断需要触发定位，保留最小元数据或按需开启。
2. **extra pid 过滤或禁止建表**：定位候选/短生命周期 pid 不应进入 E3 动态臂总落盘；若必须写，应在 run 结束 dump 时只收主 worker pid 集合与 culprit pid。
3. **周期小表分级容量**：`cpu.utilization` / `gpu.utilization` / `cpu.tasks` / `gpu.hccs` 对 P3-SW-A 的 RSS 同覆盖有用，但非 culprit 15 rank 的 420MiB 固定成本仍偏大；可以降容量或只保留 health-summary 所需窗口。
4. **短 smoke 后再全训**：B6 smoke 验收必须满足 dense=1、culprit=7、fallback=0、SET_OK、extra_pid 不进动态 dump 或显著下降，再决定是否长跑。

## 判定

本轮离线拆账判定：**PARTIAL → 需要代码/收集策略修复**。

- 链路问题：无，B5d 已证明 SQL localize + culprit-only SET 可工作。
- 数据量问题：有，固定环成本主导，`WINDOW_S` 不是第一优先级。
- 下一步：派 PR-2 B6 code/smoke，目标先消掉 main_empty 的 `step+comm` 空环和 extra_pid dump。
