# Baseline 适配 · 共通原则（轨 B）

> 形式可变（stub → 真 hook → 正式对照），**思想先钉死**。  
> 技术参考：`project/probing-test/scripts/fail-slow/platform/ascend/BASELINE_PORTING.md`、`COMPAT_MATRIX.md`；  
> 论文/开源职责：`project/reading-paper/writing/probing-paper/BASELINE-SETUP-PLAYBOOK.md`。

## 一句话目标

在昇腾上证明两件事（分开记，不要糊成一个「适配完成」）：

1. **采集（Collect）**：工具能否在训练旁路拿到**它论文/开源实现所依赖的那类数据**（非空、可复现、schema 可解析）。  
2. **检测（Detect）**：用**它自己的规则/代码路径**（不是我们的 SQL），在已知 Loud 注入上能否给出它能力范围内的判定；并标清是**自主检出**还是 **oracle 触发**。

不要求 bit-identical 于 CUDA/MetaX；要求**规则语义可迁移、可对照**。

## 红线（继承 rules，再加四条）

1. **忠实于开源实现**：论文宣传与代码不一致时，**以能跑的代码为准**；不按宣传加分。  
2. **不替对手改判据**：可改符号表 / 构建 / 依赖 / 兼容层；**不可**为抬分改它的阈值语义或把我们的答案写进它的检测器。  
3. **未穷尽 → PENDING**：缺 Redis、缺符号、镜像不齐 → `PENDING` + 卡点；**禁止**早下 `ENV-BLOCKED`（rules 红线 5）。  
4. **资源隔离**：hold-exec 在 **`yysong`** 内分 pod；标签 `yjr-as-b-<tool>-*`；不抢 Case 的 master；不碰 a3/grj；落盘 `yinjinrun.p-huawei`。

## 适配阶段机（每个工具自己的 STATUS.md 用这套）

```text
S0_ENV      镜像 / 编译器 / 依赖可见
S1_LOAD     LD_PRELOAD 或 env 启用后训练不炸（可短 iters）
S2_COLLECT  采集通道有非空输出（log/prom/jsonl/dump）
S3_RULE     用工具自带分析/规则跑通一次（可 oracle 触发，但必须标明）
S4_DETECT   在冻结 Loud 注入上给出能力范围内结论（自主或标 oracle）
S5_COST     代价五项可填（见 rules §三·五）
S6_HANDOFF  文档+产物就绪，可供 Case 轨对照波次调用
```

任一阶段失败：写 `BLOCKER`（类型用 COMPAT_MATRIX 标签：`HOOK_SYMBOL` / `STACK_CRASH` / `PENDING` / …），不要跳级宣称 S4。

## 与 Case 轨的交接

- Case 轨本阶段**不依赖** baseline。  
- 某工具进入 `S4_DETECT` 或 `S6_HANDOFF` 后，Loop 可开 **对照波次**：同一 `case_id` + 同一剂量 + 同 seed，只换工具挂载。  
- 对照结果写入 `results/ascend-ais/baseline/<tool>/` 与 ledger §3.2，**不覆盖** Case 轨已有 Probing 分数。

## 每个 Baseline Agent 的落盘约定

```text
results/ascend-ais/baseline/<tool>/
  STATUS.md           # 当前阶段、卡点、下一步（Loop 必读）
  NOTES.md            # 符号表、构建命令、踩坑
  <run_id>/           # 原始产物
project/probing-test/scripts/fail-slow/platform/ascend/<tool>/   # 可合并的脚本
```

## Loop 催办时看什么

| 若 STATUS | Loop 动作 |
|-----------|-----------|
| 卡住 >1 轮且无新 NOTES | 派 unblock 子任务或换策略（stub / 符号重扫） |
| `S2_COLLECT` 已绿 | 催做 S3/S4，不要继续只堆编译 |
| 声称完成但无非空 dump | **驳回**，退回 S2 |
| 要动 Case 池机器 | **拒绝**，改 RESOURCE 登记 |
