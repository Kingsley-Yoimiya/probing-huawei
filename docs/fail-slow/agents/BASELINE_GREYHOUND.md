# 任务卡 · Baseline Agent · Greyhound

> 专用 Agent：**只**做 Greyhound 昇腾适配。不跑 27-case 全表，不改 XPUTimer。

## 身份

- **池**：`pool-gh` → **`yysong-worker-1`**；标签 `yjr-as-b-gh-*`
- **kubectl**：`/root/.cache/volcano/kubectl/kubectl`
- **必读**：`BASELINE_COMMON.md`、`RESOURCE.md`、`platform/ascend/BASELINE_PORTING.md`、`rules.md`
- **状态文件**：`results/ascend-ais/baseline/greyhound/STATUS.md`

## 论文/实现里它该做什么（检测边界）

| 项 | 内容 |
|----|------|
| 角色 | 训中变点 + 主动验证（主对手，D-run） |
| 采集 | 经 CCL interposer（NCCL/MCCL→**HCCL**）的通信/算子侧信号；常依赖 Redis |
| 检测路径 | ACF 估周期 → 变点 → GEMM/P2P 微基准主动定位（以**开源代码真实路径**为准） |
| 能力天花板 | 常见宣传至约 **D3**；无 PID/温频时**不强行**升 D4 |
| 我们比什么 | (a) 采集非空；(b) 其规则在 Loud 注入上能否报警/定位到它能力内的对象 |

## 本阶段里程碑（按序）

1. **S0–S1**：镜像内依赖；`install_stub` 或等价 LD_PRELOAD **不炸** 16 卡短训。  
2. **S2**：有可解析输出（Redis 事件 / log / dump 任一非空）。缺 Redis → `PENDING`，继续找集群内可行部署，不写 ENV-BLOCKED。  
3. **S3**：用它自带分析脚本跑通一次（允许短窗）。  
4. **S4**：选 **1 个** Case 轨已 `LOUD_OK` 的 case（优先 P1-EXT-A 或 P3-EXT-A）做同剂量对照；记录自主 vs oracle。  
5. **S5–S6**：代价五项 + HANDOFF 说明「Case 轨如何挂 C3」。

## 允许 / 禁止

**允许**：HCCL 符号适配、stub→真 probe、Redis 部署在**自有**命名空间、改 `platform/ascend/greyhound/`。  
**禁止**：把 Case 池当调试机；写死注入窗进 Greyhound 判定；碰 `yysong-*`；宣称「适配完成」但无 S2 证据。

## 派发提示词骨架

```text
你是昇腾 Greyhound 适配 Agent。hold-exec：在 **yysong-worker-1** 上适配；标签 yjr-as-b-gh-*。
跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；勿碰 a3/grj；勿写宋 AFS；勿抢 Case 的 yysong-master。
必读 agents/{BASELINE_COMMON,BASELINE_GREYHOUND,RESOURCE}.md。
目标：至少 S2_COLLECT，争取 S4_DETECT。更新 baseline/greyhound/STATUS.md。
不跑 27-case 全表。
```

## STATUS.md 模板

```markdown
# Greyhound · STATUS
phase: S0_ENV
updated: YYYY-MM-DD HH:MM
pool_job: yjr-as-b-gh-…
collect_ok: no
detect_ok: no
oracle_trigger: n/a
blocker: none | HOOK_SYMBOL | PENDING | …
next: …
evidence: path/to/dump-or-log
```
