# Loop 状态机 · Dose Sweep + Pillar C（现役 · grj 扩池）

> 用户用 Cursor `/loop` 跑本状态机。  
> 真相源：本仓 `docs/fail-slow/` 与 `$LOCAL_RESULT_ROOT_BASE`。  
> Loud 归档：[`LOOP_LOUD.md`](LOOP_LOUD.md)。提示词：[`LOOP_PROMPT.md`](LOOP_PROMPT.md)。

## 目标（两流水线并行）

1. **流水线 1 · Dose Sweep**：Quiet/Masked @ **优先 `grj-master-0`**；GH/XPU @ yysong 空闲 worker。  
2. **流水线 2 · Pillar C**：Pilot→Runner @ **优先 `grj-worker-0`**。

`yysong` 上若有 `dyno27-*`：**改走 grj / w2**，不抢 dyno、不碰 a3。

## 每轮必读

1. [`RESOURCE.md`](RESOURCE.md)（含 grj 借用）  
2. [`../DOSE_QUEUE.md`](../DOSE_QUEUE.md)  
3. [`DOSE_SWEEP.md`](DOSE_SWEEP.md) / [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) / [`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md)  
4. [`../ledger.md`](../ledger.md) §1 + §3  
5. `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST.md`  
6. `$LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/GATE.md`（若有）

## 资源（硬 · 今晚默认）

| 池 | Pod | 同时刻 |
|----|-----|--------|
| Dose Probing | `grj-megatron-32card-0716-master-0` | ≤1 formal |
| Pillar C | `grj-megatron-32card-0716-worker-0` | ≤1 |
| Greyhound | `yysong-worker-2` | ≤1 |
| XPUTimer | 同 w2 错峰，或其它 IDLE yysong worker | ≤1 |
| Loop 父 | — | 0 卡 |

grj 落盘环境：

```bash
export POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
export POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
# 禁止写 geruijun / grj-shared-log-ckpt / 宋盘
```

**让路**：grj 上出现非我方训练 → 停我方、改派或等待；不删对方 vcjob。

## 开跑前接线

| # | 项 |
|---|-----|
| W1 | 判分按 `--dose` 取 `accept_min_ratio` |
| W2 | 每 case×档先 pilot 标定 |
| W3 | C：`GATE.md` 全绿才 Runner |
| W4 | grj：确认 IDLE + POD_BUNDLE/POD_RESULTS 指向正确 |

## 每轮决策

```text
读 DOSE_QUEUE + GATE + LOOP_LAST + 各目标 pod IDLE（含 grj 让路检查）

# Dose @ grj-master（或 IDLE 的 yysong-master）
if dose_pod IDLE:
  派 DOSE_SWEEP / CASE_RUNNER(case, dose=quiet|masked, phase=…)
  传入 hold_pod=grj-master-0 + POD_BUNDLE/POD_RESULTS

# 对照 @ yysong-w2（或其它 IDLE worker）
if gh_pod IDLE and 有 PENDING 弱档 GH → 派 BASELINE_CONTRAST(gh, …)
elif xpu_pod IDLE and 有 PENDING 弱档 XPU → 派 BASELINE_CONTRAST(xpu, …)

# C @ grj-worker
if GATE 未绿 and c_pod IDLE → 派 PILLAR_C_PILOT（可单卡小跑）
elif GATE 全绿 and c_pod IDLE → 派 PILLAR_C_RUNNER 下一单元

写 LOOP_LAST；中文短报
```

## LOOP_LAST 模板

```markdown
# LOOP_LAST
time: …
campaign: dose+pillar_c
pools: dose=grj-m0 | c=grj-w0 | gh=yysong-w2 | xpu=…
wire: W1=… W2=… W3=… W4=…
pipe1_dose: …
pipe2_c: …
grj_yield: no|yes+reason
blockers: …
next_round: …
```

## 战役成功

- Dose 代表集 Quiet+Masked：Probing + GH/XPU 终态；recipes 标定；ledger 补行  
- C：GATE 全绿 + 至少部分三臂或明确 BLOCKED  
- 无写对方盘、无删对方作业、无碰 a3；产物已回拉本机
