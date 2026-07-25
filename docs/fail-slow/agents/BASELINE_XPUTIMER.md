# 任务卡 · Baseline Agent · XPUTimer

> 专用 Agent：**只**做 XPUTimer 昇腾适配。

## 身份

- **池**：`pool-xpu` → **`yysong-worker-2`**；标签 `yjr-as-b-xpu-*`
- **kubectl**：`/root/.cache/volcano/kubectl/kubectl`
- **必读**：`BASELINE_COMMON.md`、`RESOURCE.md`、`platform/ascend/xputimer/`、`SYMBOL_MAP.md`
- **状态文件**：`results/ascend-ais/baseline/xputimer/STATUS.md`

## 论文/实现里它该做什么（检测边界）

| 项 | 内容 |
|----|------|
| 角色 | 训中常驻信号采集（E-run）；开销对照 |
| 采集 | LD_PRELOAD hook 关键算子/通信事件 → Prometheus / jsonl 等 |
| 检测路径 | 开源侧多为**采信号**；自动 RCA 若未开源则**不按宣传给 D4** |
| 能力天花板 | 常见 **D0–D1**（有信号即有分；定位能力以代码为准） |
| 我们比什么 | (a) hook 住真实符号且事件非空；(b) hang/slow 类规则在注入下是否触发 |

## 本阶段里程碑（按序）

1. **S0**：`nm -D` 填 `SYMBOL_MAP.md`（torch / HCCL / runtime）。  
2. **S1**：`build_ascend_hook.sh`（或 g++ 直编，仿 MetaX）→ preload 短训不炸。  
3. **S2**：prom/jsonl/事件计数 > 0。  
4. **S3**：自带 hang/slow 判定或最小解析脚本跑通。  
5. **S4**：对 Case 轨一个 `LOUD_OK` case 做对照；64 卡曾在 MetaX 挂死 → 昇腾先锁 **16 卡**，未经批准不上更大。  
6. **S5–S6**：代价五项 + HANDOFF。

## 允许 / 禁止

**允许**：改符号导出、直编、单卡 selftest → 2-rank → 16 卡。  
**禁止**：假设 `nccl*`/`cuda*` 同名；把 MetaX `.so` 直接当完成；占用 Case 池；未填 SYMBOL_MAP 就宣称 S4。

## 派发提示词骨架

```text
你是昇腾 XPUTimer 适配 Agent。hold-exec：在 **yysong-worker-2** 上适配；标签 yjr-as-b-xpu-*。
跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；勿碰 a3/grj；勿写宋 AFS；勿抢 Case/GH pod。
必读 agents/{BASELINE_COMMON,BASELINE_XPUTIMER,RESOURCE}.md 与 platform/ascend/{SYMBOL_MAP,xputimer}/。
先 nm → hook → S2 非空事件。更新 baseline/xputimer/STATUS.md。不跑 27-case。
```

## STATUS.md 模板

```markdown
# XPUTimer · STATUS
phase: S0_ENV
updated: YYYY-MM-DD HH:MM
symbols_filled: no
collect_ok: no
detect_ok: no
blocker: none | HOOK_SYMBOL | STACK_CRASH | PENDING | …
next: …
evidence: …
```
