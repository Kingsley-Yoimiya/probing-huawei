# 昇腾 Fail-Slow · Agent 边界包

> 给 **loop 编排器** 和 **子 Agent** 用的任务定义。方法论仍服从同目录上级的 [`../rules.md`](../rules.md)；环境事实在 [`../ledger.md`](../ledger.md)。

## 两轨并行（资源隔离）

| 轨 | 目标 | 默认规模 | 作业前缀 | 谁跑 |
|----|------|----------|----------|------|
| **A · Case** | 27-case + Probing | **yysong · 16 卡** | 标签 `yjr-as-c-*` | Case Runner |
| **B · Baseline** | 对手工具适配 | **yysong 另 worker** | 标签 `yjr-as-b-<tool>-*` | 每工具一 Agent |

- Case 本阶段只做 C0/C1/C2。Baseline 不抢 Case pod。  
- 进集群：SYY；**跑在 `yysong-*`**；落盘：`yinjinrun.p-huawei`；**勿碰 a3/grj**。

## 文件

| 文件 | 角色 |
|------|------|
| [`LOOP.md`](LOOP.md) | Loop 状态机：同时盯 Case 进度 + Baseline 适配 |
| [`CASE_RUNNER.md`](CASE_RUNNER.md) | 单 case 子 Agent 任务卡 |
| [`BASELINE_COMMON.md`](BASELINE_COMMON.md) | Baseline 适配共通原则（论文规则 / 采集 / 检测） |
| [`BASELINE_GREYHOUND.md`](BASELINE_GREYHOUND.md) | Greyhound 专用 Agent |
| [`BASELINE_XPUTIMER.md`](BASELINE_XPUTIMER.md) | XPUTimer 专用 Agent |
| [`BASELINE_FR.md`](BASELINE_FR.md) | Flight Recorder（轻轨，可后开） |
| [`RESOURCE.md`](RESOURCE.md) | 16 卡机器池划分与冲突避免 |
| [`CONCERNS.md`](CONCERNS.md) | 资源峰值 / 对沐曦隔离 / Case 三问 / Baseline 同思想 |

## Loop 一句话

每轮：**(1)** 若有空闲 Case 槽 → 派下一个 `PENDING` case；**(2)** 读各 Baseline Agent 的 `STATUS.md` → 有堵点就催/派 unblock；**(3)** 更新 [`../CASE_QUEUE.md`](../CASE_QUEUE.md) + [`../ledger.md`](../ledger.md) + `results/ascend-ais/INDEX.md`。
