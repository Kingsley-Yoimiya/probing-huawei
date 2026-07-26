# 任务卡 · Dose Sweep（B 强度维 · Quiet / Masked 两档）

> **这是什么**：把已 SCORED 的 case 从 Loud **往弱档扫**（Quiet / Masked），产出
> **检出率 vs 掩蔽强度曲线 + FPR 并排**（`OUTLINE` Fig.5 / §Eval-B）。这是 **B 支柱的强度维**，
> 不是新故障、不是 C。
>
> **方法论**：[`../rules.md`](../rules.md) **§二·五（剂量三档语义）**——先读这节。
> **复用**：[`CASE_RUNNER.md`](CASE_RUNNER.md) 三阶段全套；本卡只加 **dose 维度** + 点明两个接线点。

## 和 CASE_RUNNER 的关系（别重造）

Dose Sweep **就是 CASE_RUNNER，把 `dose` 从 loud 换成 quiet/masked**。三阶段（探索→冻结→判分）、身份、落盘、资源、三问验收**全部照 CASE_RUNNER**。本卡只补 Loud 之外多出来的东西。

> 关键纪律（rules §二·五 + 红线 3）：检测方案**已在 Loud 冻结**，弱档**只是拿冻结方案复测**——**不许因为弱档检不出就回头调 SQL/阈值**。

## 两档各测什么（rules §二·五）

| 档 | 剂量 | 期望 | 检不出算什么 |
|----|------|------|-------------|
| **Quiet** | 中（信号变弱还在） | 多数 case 仍 covered，D-level 可能降级 | 掉级如实记 |
| **Masked** | 弱（接近噪声底） | 部分 case 信号淹没 | **检不出 = 合理边界（触发悖论），不算失分**；标定"信号在多弱档消失"本身是产出 |

- **纯剂量弱**，不涉同步/结构掩蔽（那是另一回事，走定性反例，不进本卡）。
- Masked 检不出与 `INEFFECTIVE`（没咬动训练）、`ENV-BLOCKED`（没跑通）**三者分开记**。

## 两个必须接的线（Loud 没暴露、扫弱档才需要）

> 这两条是**执行前的准备**，属工具通用能力（对所有 case 一视同仁），不违红线 2。

1. **每档先 pilot 标定**：`dose_recipes.yaml` 的 quiet/masked **只有参数、无 `measured_c1_c0`/`status`**——没标定过。正式扫之前，每档先走**阶段一 pilot**：确认剂量真按预期变弱、训练咬合可测（C1/C0 落在该档 `accept_min_ratio` 附近）。标定值写回 recipes + ledger。
2. **判分按档取阈**：`score_dlevel_offline.py` 的 `d1_min_ratio` / `accept_loud.py` 的 `--min-ratio` 现在**按 Loud 默认**。扫弱档要让它们**按 `--dose` 从 recipes 取对应 `accept_min_ratio`**（loud 1.3 / quiet 1.15 / masked 1.05 之类）。**这条接线先做**，否则弱档会拿 Loud 阈值误判成全 D0。

## 跑哪些 case（已 SCORED 的往下扫）

- **只扫已 Loud SCORED 且 dose calibrated 的 case**（ledger §3.1 里那些）——它们检测方案已冻结，可复测。
- **排除已知 `injection_ineffective`**（如 P3-EXT-B「非常大噪声」那种 Loud 都咬不动的）——Loud 都不进分母，弱档更无意义。
- 未 SCORED 的 case：先走 Loud（CASE_RUNNER），不在本卡范围。

## 对手也扫两档（对齐 §三·五 A）

- Greyhound / XPUTimer 同样跑 quiet/masked，**同样在 Loud 冻结阈值后复测弱档**，不替它调。
- 产出对手的检出率随掩蔽强度曲线，和 Probing 并排 → 预期对手在弱档更早掉到 0。
- 走现有 [`BASELINE_CONTRAST.md`](BASELINE_CONTRAST.md)，dose 换 quiet/masked。

## 资源与轨（独立于 C，不抢）

- Dose Sweep 是 **Case 性质**，用 **master-0**（±worker-0 若 C 没在用）；对手对照用 worker-1/worker-2——**和 Loud Case 同池，本来就串行排队**。
- **C 用 worker-0**；两轨错开 pod，互不抢。若 worker-0 被 C 占，Dose Sweep 的对手对照排 worker-1/2 队列。

## 产出

| 产物 | 内容 |
|------|------|
| 标定 | recipes 两档补 `measured_c1_c0`/`status=calibrated` |
| 曲线 | 每 case：loud/quiet/masked 三点 D-level → 检出率 vs 掩蔽强度 |
| FPR 并排 | 健康作业（C0）在各档阈值下的假阳性率 |
| 对手曲线 | GH/XPU 同 case 三档，与 Probing 并排 |
| ledger | §3 每 case 补 quiet/masked 行 + 边界值（信号在哪档消失） |

## 派发提示词骨架

```text
你是「昇腾 Fail-Slow Dose Sweep」执行者。任务=把已 Loud SCORED 的 case 往 Quiet/Masked 弱档扫，出检出率vs掩蔽强度曲线+FPR。这是 B 强度维，不是新故障、不是 C。
先读 rules §二·五（三档语义）：Loud 冻结的检测方案弱档只复测、不回调（红线3）；Masked 检不出=合理边界如实记，不算失分。
【两个接线点，先做】①每档先 pilot 标定（recipes quiet/masked 只有参数无 measured）；②判分脚本按 --dose 从 recipes 取 accept_min_ratio（现按 loud 默认，不接会全误判 D0）。
复用 CASE_RUNNER 三阶段/身份/落盘/资源；dose=quiet|masked。只扫已 SCORED+calibrated 的 case，排除 injection_ineffective（如 P3-EXT-B）。对手 GH/XPU 同扫两档、同冻结阈值复测（BASELINE_CONTRAST）。
source env.sh；SYY；Case 用 master-0（±worker-0 若 C 没占）；勿抢 C 的 worker-0、勿碰 a3/grj；落盘 yinjinrun.p-huawei。产物回拉 + recipes 标定值 + ledger 两档行 + 边界值。守红线。
```
