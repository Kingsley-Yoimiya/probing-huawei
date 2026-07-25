# Loop 状态机 · 昇腾双流水线

> 用户用 Cursor `/loop`（或等价）跑本状态机。  
> 维护者可从 myportal 开聊；**真相源**在本仓 `docs/fail-slow/` 与 `$LOCAL_RESULT_ROOT_BASE`（默认 `results/ascend-ais/`）。对外不依赖 myportal。

## 目标（两流水线并行）

1. **流水线 1 · Case（Probing）**：按 [`../CASE_QUEUE.md`](../CASE_QUEUE.md) 扫可跑 27 格；已 `SCORED` **跳过**；第三梯队标 `SKIP_PERM`。  
2. **流水线 2 · Baseline 对照**：按 [`../CONTRAST_QUEUE.md`](../CONTRAST_QUEUE.md) 对已冻结 dose 的 case 跑 Greyhound / XPUTimer；**不改** Probing 分。

适配（S0–S4）已完成；本战役 Loop **不再**以「推适配」为主，只派 **Case Runner** 与 **Baseline Contrast**。

## 每轮必读（短）

1. [`../CASE_QUEUE.md`](../CASE_QUEUE.md)  
2. [`../CONTRAST_QUEUE.md`](../CONTRAST_QUEUE.md)  
3. [`../ledger.md`](../ledger.md) §1.3 + §3  
4. `$LOCAL_RESULT_ROOT_BASE/INDEX.md`（若有）  
5. `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST.md`（若有）  
6. `baseline/{greyhound,xputimer}/STATUS.md`  
7. [`RESOURCE.md`](RESOURCE.md)

## 资源（硬）

| 池 | Pod | 同时刻 |
|----|-----|--------|
| Case | `yysong-master-0` | formal ≤1 |
| Greyhound 对照 | `yysong-worker-1` | ≤1 |
| XPUTimer 对照 | `yysong-worker-2` | ≤1 |
| Loop 父 | — | 0 卡；只派 Task |

空闲 = 目标 pod 内无活 `torchrun`。禁止碰 a3/grj。

## 每轮决策（伪代码）

```text
读 CASE_QUEUE + CONTRAST_QUEUE + STATUS + LOOP_LAST + RESOURCE

# —— 流水线 1：Case @ master-0 ——
if master IDLE:
  if 第三梯队仍有未标 SKIP_PERM:
    本轮批量写入 SKIP_PERM（不进分母、不进对照）
  elif 存在可跑且非终态（PENDING/PILOT/LOUD_OK 待 score）:
    pick = 第二梯队优先序中首个
    派 CASE_RUNNER(case_id)   # 无 dose → 先移植再 Loud
  # 已 SCORED：不派 Case；确认已出现在 CONTRAST_QUEUE

# —— 流水线 2：对照 @ workers ——
for (tool, pod) in [(gh, w1), (xpu, w2)]:
  if pod IDLE and CONTRAST_QUEUE 存在该 tool 的 PENDING:
    pick = 队头（P3-EXT-A×GH 公平性重跑优先；其余已 SCORED）
    派 BASELINE_CONTRAST(tool, case_id, frozen_dose, case_ref)

写 LOOP_LAST；中文短报用户
```

## 子 Agent 边界

| Agent | 任务卡 | 可写 | 不可写 |
|-------|--------|------|--------|
| Case Runner | [`CASE_RUNNER.md`](CASE_RUNNER.md) | master-0、Case 结果、CASE_QUEUE | workers 对照、a3/grj |
| Baseline Contrast | [`BASELINE_CONTRAST.md`](BASELINE_CONTRAST.md) | 对应 worker、`baseline/<tool>/contrast-*`、CONTRAST_QUEUE | master-0、改 Probing 分 |
| Loop 父 | 本文件 + [`LOOP_PROMPT.md`](LOOP_PROMPT.md) | 派发、LOOP_LAST、台账催更 | 自己长跑占卡 |

## LOOP_LAST 模板

```markdown
# LOOP_LAST
time: …
pipe1_case: dispatched=… | skipped_scored=… | skip_perm=… | monitored=…
pipe2_contrast: gh=… | xpu=… | queue_pending=…
blockers: …
next_round: …
```

## 战役成功（可停 loop）

- Case：可跑格均 `SCORED`/`INEFFECTIVE`；第三梯队 `SKIP_PERM` 写齐。  
- 对照：所有 `calibrated` case 的 GH×XPU 在 CONTRAST_QUEUE 为 `DONE`（或 `BLOCKED`+原因）。  
- 全程无串池、无碰他人作业、无写宋 AFS。

## /loop 提示词

见 [`LOOP_PROMPT.md`](LOOP_PROMPT.md)。
