# Loop 状态机 · Param-Calib（参数标定 · 2026-07-26 开）

> 用户用 Cursor `/loop` 跑本状态机。
> **方案**：`project/reading-paper/writing/probing-paper/PILLAR-C-PARAM-CALIBRATION-PLAN.md`。
> 队列：[`../PARAM_CALIB_QUEUE.md`](../PARAM_CALIB_QUEUE.md) · 任务卡：[`PARAM_CALIB_RUNNER.md`](PARAM_CALIB_RUNNER.md)。
> **和 Pillar-C v2 的关系**：v2 主线已收官；本 loop 往下钻一层，**把每个模块的参数用数据标定出来**。**不抢 v2 的 grj-w0 活作业**。

## 目标

把 4 模块的关键参数（判据阈 / 追溯窗 W\* / 升精度 rate / 多机去噪）**从数据推出来**，回答"为什么这么设"。主线 = **批次1（离线）→ P-fix → 批次2 → 批次3**。

## 每轮必读

1. [`RESOURCE.md`](RESOURCE.md) + [`BUILD_WHEEL.md`](BUILD_WHEEL.md)（凡涉及重编 wheel）
2. [`../PARAM_CALIB_QUEUE.md`](../PARAM_CALIB_QUEUE.md)
3. [`PARAM_CALIB_RUNNER.md`](PARAM_CALIB_RUNNER.md)
4. [`../ledger.md`](../ledger.md) §4.1
5. `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST_PARAM_CALIB.md`
6. `$LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/MECH_FIX.md`（P-fix 状态）

## 资源（硬）

| 池 | Pod | 同时刻 |
|----|-----|--------|
| 批次1（离线标定） | **本地**（读 jsonl，不占卡） | 可并行多个 |
| 批次2/3（集群） | `grj-megatron-32card-0716-worker-0`（v2 IDLE 时借） | ≤1 |
| Loop 父 | — | 0 卡 |

**让路**：grj-w0 有 v2 或对方训练在跑 → 批次2/3 等；批次1 离线不受影响照跑。不删对方 vcjob、不写 geruijun/宋盘。

## 每轮决策

```text
读 PARAM_CALIB_QUEUE + MECH_FIX + LOOP_LAST + grj-w0 状态

# 批次1 优先（纯离线，不占卡，随时可跑）
if 批次1 有未完成（①-A/①-B/②-B）:
  派 PARAM_CALIB_RUNNER(exp_id)  # 本地读现有 run，出 PARAM.json+曲线
  # ①-A 档阈曲线最先——最能证"参数由数据定"

# 批次2 需 P-fix 且 grj-w0 空
elif P-fix 未绿:
  派 Code/P-fix（修 SET 真相键 probing.torch.profiling= + 关键表环调大 + 尖刺标定）→ MECH_FIX.md
elif grj-w0 IDLE（让路检查通过）:
  按队列派 批次2（②-A W* → ③-A/③-B 升精度）

# 批次3 需先定健康摘要判据
elif 批次3 判据已定 and 多机可用:
  派 ④-A/④-B

写 LOOP_LAST_PARAM_CALIB；中文短报；父不自己开训
```

## LOOP_LAST 模板

```markdown
# LOOP_LAST_PARAM_CALIB
time: …
campaign: param_calib
pools: offline=local | cluster=grj-w0(借v2空档)
batch1: ①-A=… ①-B=… ②-B=…
batch2: P-fix=… ②-A=… ③-A=… ③-B=…
batch3: ④-A=… ④-B=…
grj_yield: no|yes(v2在跑)
blockers: …
next_round: …
```

## 战役成功（可停 loop）

- 批次1：①-A 出档阈 FPR–召回曲线（三档 θ\*）、①-B 出定位阈、②-B 出环容量换算——**全部有 PARAM.json + 曲线**。
- 批次2：P-fix 绿后 ②-A 4 case 都出 W\*、③ 出升精度 rate/延迟。
- 批次3：④ 出朴素vs联邦去噪量。
- 每个参数都能回答"为什么这么设"（数据支撑）。
- 无改 v2 文件、无抢 grj-w0 活作业、无碰 a3/宋盘；产物回拉 `results/ascend-ais/param_calib/`。

## /loop 提示词

```text
你是「昇腾 Param-Calib Loop」编排器。目标=把 4 模块参数用数据标定出来（判据阈/追溯窗/升精度/多机去噪）。
只读状态、决策、派 sub-agent、写 LOOP_LAST_PARAM_CALIB；不自己开训、不占卡。
必读：docs/fail-slow/PARAM_CALIB_QUEUE.md + agents/{PARAM_CALIB_RUNNER,LOOP_PARAM_CALIB,RESOURCE}.md + PILLAR-C-PARAM-CALIBRATION-PLAN.md。
决策：批次1（①-A档阈/①-B定位阈/②-B环容量）纯离线优先派（不占卡，①-A最先）；P-fix未绿则先修（SET真相键+关键表环+尖刺）；grj-w0 IDLE且让路通过才派批次2；批次3需先定健康摘要判据。
让路：grj-w0 有 pillar_c_v2 或对方训练在跑 → 批次2/3 等，批次1 照跑。产物 results/ascend-ais/param_calib/。写 LOOP_LAST 中文短报。
```
