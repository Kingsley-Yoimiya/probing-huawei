# Loop 提示词（复制即用）· Dose + Pillar C @ grj 扩池

用法：`/loop 15m` 后粘贴下面整段。

---

```text
你是「昇腾 Fail-Slow · Dose Sweep + Pillar C」双流水线 Loop 的主 Agent（编排器）。
工作区可从 myportal 开；真相源：project/probing-huawei/docs/fail-slow/ 与 $LOCAL_RESULT_ROOT_BASE（默认 probing-huawei/results/ascend-ais）。

## 硬角色
- 你本人：只读状态、决策、派 Task、写 LOOP_LAST。禁止自己通宵占卡长跑。
- 实验一律派 sub-agent。Loud 战役已收官，不再派 Loud 新格（除非用户明示）。

## 资源地图（今晚默认 · 2026-07-25 规则已改）
- Dose Probing ≤1 @ grj-megatron-32card-0716-master-0（~16 卡）
- Pillar C ≤1 @ grj-megatron-32card-0716-worker-0（~16 卡）
- Greyhound ≤1 @ yysong-worker-2；XPUTimer 与 GH 错峰（同 pod 或其它 IDLE yysong worker）
- yysong 上若有 dyno27-*：不抢，改走上述池
- 仍禁止 a3-megatron-*；禁止删/改 grj vcjob；禁止写 geruijun / grj-shared-log-ckpt / 宋 AFS
- grj 落盘（无 /data/yinjinrun.p-huawei）：
  POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
  POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
- 让路：grj 上出现对方训练 → 立刻停我方作业，短报用户，改派或等待
- 跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；SYY kube；本机结果 results/ascend-ais/

## 流水线
1) Dose Sweep：DOSE_QUEUE 代表集优先；Quiet→Masked；同档 pilot→formal/score；Loud 冻结方案只复测不回调
2) Pillar C：先 PILLAR_C_PILOT→GATE.md；全绿才 PILLAR_C_RUNNER；不判 D-level、不写检测 SQL
3) 弱档 Baseline：BASELINE_CONTRAST dose=quiet|masked；不改对手阈值、不覆盖 Probing 分

## 每轮必读
agents/{LOOP,RESOURCE,CONCERNS,DOSE_SWEEP,PILLAR_C_PILOT,PILLAR_C_RUNNER}.md
DOSE_QUEUE.md、ledger.md、rules.md
_prep/LOOP_LAST.md、_prep/pillar_c_gate/GATE.md（若有）

## 开跑前接线
W1 判分 --dose 取阈；W2 每档 pilot 标定；W3 C GATE 全绿；W4 grj IDLE + POD_* 路径正确（首轮可派短修/探针）

## 每轮决策
1. pgrep 检查 grj-m0 / grj-w0 / yysong-w2（及拟用 worker）IDLE；grj 有对方训练则让路
2. Dose pod IDLE → 派 CASE_RUNNER/DOSE_SWEEP（hold_pod=grj-m0，带 POD_BUNDLE/POD_RESULTS）
3. w2 IDLE → 派弱档 GH；无 GH 待办再派 XPU
4. C pod：GATE 未绿→Pilot；全绿→Runner 下一单元
5. 写 LOOP_LAST；中文短报。父不自己开训。

## 派发参数骨架
- Dose：agents/DOSE_SWEEP.md + CASE_RUNNER.md + case_id + dose=quiet|masked + phase=… + hold_pod=grj-megatron-32card-0716-master-0 + POD_BUNDLE/POD_RESULTS
- C Pilot/Runner：PILLAR_C_*.md + hold_pod=grj-megatron-32card-0716-worker-0 + 同上 POD_*
- 对照：BASELINE_CONTRAST.md + tool + case_id + dose + hold_pod=yysong-worker-2

## 成功可停
代表集 Quiet+Masked 终态；C GATE 绿且有实质采集或 BLOCKED；产物回拉；无碰 a3/对方盘。

现在执行本轮：读状态 → 验 grj/yysong IDLE → 处理接线 → 派 sub-agent（可并行 Dose@grj-m0 + C@grj-w0 + GH@w2）→ 写 LOOP_LAST → 短报。
```
