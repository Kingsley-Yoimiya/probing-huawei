# 昇腾 Fail-Slow · Agent 边界包

> 给 **loop 编排器** 和 **子 Agent** 用的任务定义。方法论仍服从 [`../rules.md`](../rules.md)；环境事实在 [`../ledger.md`](../ledger.md)。

## 现役战役（2026-07-25 起）：Dose Sweep ∥ Pillar C

| 流水线 | 目标 | 落点 | 谁跑 |
|--------|------|------|------|
| **1 · Dose Sweep** | Quiet/Masked：Probing + GH/XPU | **grj-m0** + yysong-w2 | Dose + Baseline Contrast |
| **2 · Pillar C** | 门禁 → 三臂 + 三场景 | **grj-w0** | Pilot → Runner |

- Loud 已收官 → [`LOOP_LOUD.md`](LOOP_LOUD.md)。  
- 进集群：SYY；落盘：`yinjinrun.p-huawei`；**可空闲借 grj**；**仍禁 a3**。

## 文件

| 文件 | 角色 |
|------|------|
| [`LOOP.md`](LOOP.md) | **现役**双流水线状态机（Dose + C） |
| [`LOOP_PROMPT.md`](LOOP_PROMPT.md) | `/loop` 可粘贴提示词 |
| [`LOOP_LOUD.md`](LOOP_LOUD.md) | Loud 战役归档（勿再开） |
| [`DOSE_SWEEP.md`](DOSE_SWEEP.md) | B 强度维任务卡 |
| [`../DOSE_QUEUE.md`](../DOSE_QUEUE.md) | Quiet/Masked 队列 |
| [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) | C 机制门禁 |
| [`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md) | C 正式采集（硬依赖 GATE 全绿） |
| [`CASE_RUNNER.md`](CASE_RUNNER.md) | Dose 复用（dose=quiet\|masked） |
| [`BASELINE_CONTRAST.md`](BASELINE_CONTRAST.md) | 弱档对照（dose 换档） |
| [`RESOURCE.md`](RESOURCE.md) | 机器池 |
| [`CONCERNS.md`](CONCERNS.md) | 隔离与三问 |
| [`BASELINE_COMMON.md`](BASELINE_COMMON.md) 等 | 适配期参考（归档） |

## Loop 一句话

每轮：**(1)** master 空 → 派下一 Dose 格；**(2)** w1/w2 空 → 派弱档对照；**(3)** w0：GATE 未绿派 Pilot，全绿派 Runner；**(4)** 更新 DOSE_QUEUE + GATE + ledger + LOOP_LAST。
