# 昇腾 Fail-Slow · Agent 边界包

> 给 **loop 编排器** 和 **子 Agent** 用的任务定义。方法论仍服从 [`../rules.md`](../rules.md)；环境事实在 [`../ledger.md`](../ledger.md)。

## 双流水线（资源隔离）

| 流水线 | 目标 | 落点 | 谁跑 |
|--------|------|------|------|
| **1 · Case** | 27-case + Probing C0/C1/C2 | `yysong-master-0` · `yjr-as-c-*` | Case Runner |
| **2 · 对照** | 冻结 dose 上 GH / XPU 检出 | worker-1 / worker-2 · `yjr-as-b-*` | Baseline Contrast |

- 已 SCORED 的 case **跳过**流水线 1，直接进 [`../CONTRAST_QUEUE.md`](../CONTRAST_QUEUE.md)。  
- 适配 S0–S4 已完成；本波不派「再适配」。FR/Dynolog 本波不进对照。  
- 进集群：SYY；落盘：`yinjinrun.p-huawei`；**勿碰 a3/grj**。

## 文件

| 文件 | 角色 |
|------|------|
| [`LOOP.md`](LOOP.md) | 双流水线状态机 |
| [`LOOP_PROMPT.md`](LOOP_PROMPT.md) | `/loop` 可粘贴提示词 |
| [`CASE_RUNNER.md`](CASE_RUNNER.md) | 流水线 1 任务卡 |
| [`BASELINE_CONTRAST.md`](BASELINE_CONTRAST.md) | 流水线 2 任务卡 |
| [`BASELINE_COMMON.md`](BASELINE_COMMON.md) | 竞品公平原则 |
| [`BASELINE_GREYHOUND.md`](BASELINE_GREYHOUND.md) / [`BASELINE_XPUTIMER.md`](BASELINE_XPUTIMER.md) | 适配期任务卡（归档参考） |
| [`RESOURCE.md`](RESOURCE.md) | 机器池 |
| [`CONCERNS.md`](CONCERNS.md) | 隔离与三问 |

## Loop 一句话

每轮：**(1)** master 空 → 跳过已 SCORED / 标 SKIP_PERM / 派下一 PENDING case；**(2)** worker 空 → 派 CONTRAST_QUEUE 队头；**(3)** 更新 CASE_QUEUE + CONTRAST_QUEUE + ledger + LOOP_LAST。
