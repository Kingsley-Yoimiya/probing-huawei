# Loop 状态机 · 昇腾双轨编排

> 用户用 Cursor `/loop`（或等价）跑本状态机。  
> **父会话留在 myportal**；写盘与身份服从 `AGENTS.md` + 华为 `ledger.md`。

## 目标

每一轮 loop 同时推进：

1. **轨 A**：16 卡上按队列跑 27-case（Probing C0/C1/C2），出分。  
2. **轨 B**：检查 Greyhound / XPUTimer（及可选 FR）适配 STATUS，推动 unblock，直到可采集、可按论文规则检测。

Baseline **未就绪时不阻塞** Case 遍历。

## 每轮必读（短）

1. `docs/fail-slow/CASE_QUEUE.md`  
2. `docs/fail-slow/ledger.md` §1.3 门禁 + §3  
3. `results/ascend-ais/INDEX.md`  
4. `results/ascend-ais/baseline/*/STATUS.md`（若尚无文件 → 视为 S0，应派适配 Agent）  
5. `agents/RESOURCE.md`（**hold-exec on yysong**：壳空闲即可派）

## 状态变量（逻辑）

```text
case_queue:     CASE_QUEUE 中非终态条目
case_inflight:  yysong Case pods 内是否有我们的活训练
gh_phase / xpu_phase / fr_phase:  来自各 STATUS.md
shell_idle:     yysong 目标 pods 无活 torchrun
```

终态（Case）：`SCORED` | `SKIP_PERM` | `INEFFECTIVE`  
进行中：`PENDING` | `PILOT` | `LOUD_OK`

## 每轮决策（伪代码）

```text
# --- 轨 A：Case ---
if yysong Case pods 空闲 and 存在可跑 case:
  pick = 第一梯队中首个 PENDING/PILOT/待 score
  派生子 Agent ← CASE_RUNNER.md + case_id（hold-exec on yysong）
elif case_inflight:
  只监控 / 催回拉
# 禁止：因调度空闲=0 不派；禁止改去 a3/grj

# --- 轨 B：Baseline ---
在 yysong-worker-1 / worker-2 推进 GH / XPU（错开 Case）
```

## 派发优先级

1. 门禁（SSH/kube/kubectl）红 → 先修  
2. Case 第一梯队 —— **yysong 空闲即派**  
3. Baseline S0→S2 —— 并行（另 yysong worker）  
4. S3→S4 / 对照波次 —— 后

## 子 Agent 边界（硬）

| Agent | 可写 | 不可写 |
|-------|------|--------|
| Case Runner | `yysong` Case pods、`results/ascend-ais/<run_id>/` | a3/grj、宋 AFS、muxi-h3c |
| Greyhound | `yysong-worker-1`、`baseline/greyhound/` | Case 正在用的 pod、a3/grj |
| XPUTimer | `yysong-worker-2`、`baseline/xputimer/` | 同上 |
| Loop 父 | 派发、台账 | 自己长跑占满 |

跳板：`K=/root/.cache/volcano/kubectl/kubectl` + SYY kube。

## LOOP_LAST.md 模板

```markdown
# LOOP_LAST
time: …
case: dispatched=… | monitored=… | updated=…
baseline: gh=Sx … | xpu=Sx … | actions=…
blockers: …
next_round: …
```

## 成功判据（战役级，非单轮）

- Case：第一梯队有 Loud 分；27 格中可跑者尽量 `SCORED`/`SKIP_PERM`/`INEFFECTIVE` 有归宿  
- Baseline：至少 Greyhound 与 XPUTimer 各自达到 **S2_COLLECT**；争取 S4；未达者不阻塞 Case 分母  
- 全程无写他人 AFS / 无碰 `yysong-*` / 无混写 `muxi-h3c`

## /loop 提示词（父会话 · 全文可粘贴）

见同目录 [`LOOP_PROMPT.md`](LOOP_PROMPT.md)。