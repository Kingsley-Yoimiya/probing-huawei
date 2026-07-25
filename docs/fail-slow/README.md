# Fail-Slow × 华为昇腾 — 文档入口

> **平台**：Ascend NPU / 华为 AIS（`vc-a3-241ceshi`）  
> **方法论**：与沐曦同构；本目录是华为侧的规则 + 台账副本。

| 文件 | 作用 |
|------|------|
| [`SHARE.md`](SHARE.md) | **对外入口**：三仓链接、台账、竞品代码、结果路径、最小配置 |
| [`rules.md`](rules.md) | 不变的方法论（红线 / 控变 / 三阶段 / D0–D5） |
| [`ledger.md`](ledger.md) | 华为环境门禁、剂量、已跑 case 速览（Agent 边跑边改） |
| [`CASE_QUEUE.md`](CASE_QUEUE.md) | 27-case 排期与权限跳过表 |
| [`agents/`](agents/README.md) | **双轨 Agent 边界** + Loop 状态机（Case 16 卡 / Baseline 另池） |

## 和沐曦的分工（别混）

| 层 | 沐曦 | 华为昇腾 |
|----|------|----------|
| 规则 / 台账 | `probing-test/docs/fail-slow/` | **本目录** |
| Probing 包 | MetaX 构建 / Probing_plus | **本仓**（NPU backend） |
| 编排脚本 | `probing-test/scripts/fail-slow/` | 同级 `probing-test` + `platform/ascend/`；本仓 `scripts/fail-slow/` 薄包装 |
| 结果本机根 | （沐曦侧自定） | **本仓** `results/ascend-ais/`（可用 `LOCAL_RESULT_ROOT_BASE` 覆盖） |
| 落盘前缀 | `…/yinjinrun.p/…` | `…/yinjinrun.p-huawei/…`（pod 常为 `/data/yinjinrun.p-huawei/`） |
| 进集群 | 默认同人 / 借权 | **借 songyiyang（SYY）**；见 [`IDENTITY.md`](IDENTITY.md) |

**对外不依赖 myportal**（私有编排仓）。协作者入口：[`SHARE.md`](SHARE.md)。

故障定义真相源仍是论文侧  
`project/reading-paper/writing/probing-paper/OUTLINE-v3-27-cases-per-cell.md`。

## Agent 开跑顺序

1. 读本目录 `rules.md` + `ledger.md` 门禁  
2. 读 [`agents/README.md`](agents/README.md)：轨 A Case（16 卡）与轨 B Baseline **资源隔离**  
3. 同级 clone `probing-test`（或 `export FS_SHARED_SCRIPTS=…`）  
4. `source scripts/fail-slow/env.sh`（本仓；默认结果根=本仓 `results/ascend-ais/`）  
5. 跳板 + SYY kube：[`IDENTITY.md`](IDENTITY.md)  
6. Case / Baseline 任务卡：[`agents/`](agents/README.md)  
7. 产物：`$LOCAL_RESULT_ROOT_BASE/<run_id>/`；Baseline → `…/baseline/<tool>/STATUS.md`  

本阶段 Case **不跑** baseline 对照；Baseline 未就绪**不阻塞** 27-case 遍历。
