# Fail-Slow × 华为昇腾 — 文档入口

> **平台**：Ascend NPU / 华为 AIS（`vc-a3-241ceshi`）  
> **方法论**：与沐曦同构；本目录是华为侧的规则 + 台账副本。

| 文件 | 作用 |
|------|------|
| [`rules.md`](rules.md) | 不变的方法论（红线 / 控变 / 三阶段 / D0–D5） |
| [`ledger.md`](ledger.md) | 华为环境门禁、剂量、已跑 case 速览（Agent 边跑边改） |
| [`CASE_QUEUE.md`](CASE_QUEUE.md) | 27-case 排期与权限跳过表 |
| [`agents/`](agents/README.md) | **双轨 Agent 边界** + Loop 状态机（Case 16 卡 / Baseline 另池） |

## 和沐曦的分工（别混）

| 层 | 沐曦 | 华为昇腾 |
|----|------|----------|
| 规则 / 台账 | `project/probing-test/docs/fail-slow/` | **本目录** |
| Probing 包 | MetaX 构建 / Probing_plus | **本仓** `probing-huawei`（NPU backend） |
| 编排脚本 | `probing-test/scripts/fail-slow/` | 共享编排 + `platform/ascend/`；本仓 `scripts/fail-slow/` 只放 env / dose / 薄包装 |
| 结果本机根 | `results/muxi-h3c/` | `results/ascend-ais/` |
| AFS | `…/yinjinrun.p/…` | `…/yinjinrun.p-huawei/…` |
| 进集群身份 | 默认同人 / 借 weibozhen | 默认同人 / **借 songyiyang（SYY）** 拿 128 卡面 |

故障定义真相源仍是论文侧  
`project/reading-paper/writing/probing-paper/OUTLINE-v3-27-cases-per-cell.md`。

## Agent 开跑顺序

1. 读本目录 `rules.md` + `ledger.md` 门禁  
2. 读 [`agents/README.md`](agents/README.md)：轨 A Case（16 卡）与轨 B Baseline **资源隔离**  
3. `source scripts/fail-slow/env.sh`（本仓）  
4. 跳板 `ais-cf3e61a5` + SYY kube（`huawei-ais-syy`）  
5. 开 `/loop` 时用 [`agents/LOOP.md`](agents/LOOP.md) 提示词；子任务用 CASE_RUNNER / BASELINE_* 任务卡  
6. Case 结果 → `results/ascend-ais/<run_id>/`；Baseline 状态 → `results/ascend-ais/baseline/<tool>/STATUS.md`  

本阶段 Case **不跑** baseline 对照；Baseline 未就绪**不阻塞** 27-case 遍历。
