# Fail-Slow × 华为昇腾 — 文档入口

> **平台**：Ascend NPU / 华为 AIS（`vc-a3-241ceshi`）  
> **方法论**：与沐曦同构；本目录是华为侧的规则 + 台账副本。

| 文件 | 作用 |
|------|------|
| [`SHARE.md`](SHARE.md) | **对外入口**：两公开仓、身份、无 myportal |
| [`IDENTITY.md`](IDENTITY.md) | kube / 跳板 / 落盘约定 |
| [`rules.md`](rules.md) | 不变的方法论（红线 / 控变 / 三阶段 / D0–D5） |
| [`ledger.md`](ledger.md) | 华为环境门禁、剂量、已跑 case 速览 |
| [`CASE_QUEUE.md`](CASE_QUEUE.md) | 27-case Loud 排期（已收官参考） |
| [`CONTRAST_QUEUE.md`](CONTRAST_QUEUE.md) | Loud 竞品对照队列（已收官参考） |
| [`DOSE_QUEUE.md`](DOSE_QUEUE.md) | **现役流水线 1**：Quiet/Masked 队列 |
| [`LOOP.md`](LOOP.md) | → [`agents/LOOP.md`](agents/LOOP.md)（Dose ∥ Pillar C） |
| [`agents/`](agents/README.md) | 现役任务卡 + `/loop` 提示词 |

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

## Agent 开跑顺序（现役 · Dose + Pillar C）

1. 读 `rules.md` + `ledger.md` 门禁  
2. 读 [`agents/LOOP.md`](agents/LOOP.md) + [`DOSE_QUEUE.md`](DOSE_QUEUE.md)  
3. 同级 clone `probing-test`（或 `export FS_SHARED_SCRIPTS=…`）  
4. `source scripts/fail-slow/env.sh`  
5. 跳板 + SYY kube：[`IDENTITY.md`](IDENTITY.md)  
6. `/loop 15m` 粘贴 [`agents/LOOP_PROMPT.md`](agents/LOOP_PROMPT.md)  

流水线 1（Dose @ master/w1/w2）与 流水线 2（Pillar C @ worker-0）**并行**。  
Loud 战役见 [`agents/LOOP_LOUD.md`](agents/LOOP_LOUD.md)（归档，勿再开）。
