# 健康机摘要判据 LOCKED

> 状态：**LOCKED** · `4_health_summary_criteria` · 2026-07-27

## 一句话

C0 本地 FPR(loud)=0.000% vs 预算 1%；注入臂 victim∈suspects 召回=1.0; 单 victim 时 n_suspect≈1 → 联邦量比≈0.06257106085967849

## 1. 什么算健康机

两层：① 本地摘要层——rank 自比 step_ms 窗中位/稳态基线 < θ*_dose 则自称 healthy；② 协调明细层——仅当 dose 门控过线后，用 ①-B 跨 rank max/min≥θ* 或 worst_fraction≥φ* 标出的 suspect 才拉明细。健康机=非 suspect。

| 层 | 字段 | 窗口 | 阈值 | 来源 |
|---|---|---|---|---|
| 本地摘要 | `step_ms` 中位 / 稳态基线 | 检测 [100, 300]；基线 [50, 99] | θ*_dose = {'loud': 1.16, 'quiet': 1.12, 'masked': 1.04}（④-A 默认 loud **1.16**） | ①-A |
| 协调 suspect | 跨 rank phase max/min；worst_fraction | 同检测窗 | θ*=**1.2**；φ*=**0.4** | ①-B |
| dose 门控 | rank0 `step_ms` C1/C0 | [100, 300] | 同 θ*_dose | ①-A |

可执行谓词：

```
median(step_ms[W])/median(step_ms[steady]) < θ*_dose
dose_gate(C1/C0 step rank0 ≥ θ*_dose) ∧ (cross_rank_phase_maxmin ≥ 1.2 ∨ worst_fraction ≥ 0.4) → pred ranks
return DETAIL iff rank ∈ suspects else SUMMARY
```

## 2. 健康机回传什么（摘要）

- 字节量级预期：**~180 B/rank**（紧凑 JSON「我正常」摘要，无 torch_trace / 无逐步序列）
- schema：

```json
{
  "rank": "int",
  "status": "\"healthy\"",
  "step_ms_med": "float",
  "baseline_med": "float",
  "step_ratio": "float",
  "phase_metric": "str  # compute_ms|data_ms|…",
  "phase_med": "float",
  "window": "[lo,hi]",
  "dose_theta": "float  # θ* used",
  "ts": "int|float"
}
```

## 3. 非健康机（suspect）回传什么（明细）

- phase 窗明细 ~**14000 B**；含 TT W* 时 ~**2533040 B**（W*=100，B/step≈25190）
- 范围：{"always": ["step_ms series for window [trigger-W*+1, trigger] (W*=100)", "phase: compute_ms/comm_ms/wait_ms/data_ms same window"], "if_upgraded": ["python.torch_trace rows in W* at rate*=0.001"], "not_returned_by_healthy": ["torch_trace / span detail", "full step series beyond summary scalars"]}
- 只回与异常相关的窗明细 +（若已升详）W* torch_trace；非全表 dump

## 4. 假阳性预算

| 项 | 值 |
|---|---|
| 定义 | 健康作业(C0)上，本地摘要层将 rank 误判为不健康的比例；协调侧误标 suspect 另计 |
| 预算（④-A 默认 loud） | **≤ 1%**（对齐 ①-A B_loud） |
| 分档预算 | loud 1% / quiet 5% / masked 12% |
| 实测 C0 本地 FPR（loud 均值） | **0.000%** |
| GPU 层 C0 FPR | 0.0 |
| Host 层 C0 FPR | 0.0 |
| C0 跨 rank 误火率 | 0.2 |
| 是否压进预算 | True |

## 5. 离线验证摘要

- 验证 run 数：5；注入臂：10
- victim∈suspects 召回：**1.0**
- 均值 n_suspects：**1**
- 注入下非 victim 本地健康率均值：0.0（注入下非 victim 的 step_ms 常一起升高→纯本地 step 会把多数 rank 标不健康；故明细门必须走协调侧 ①-B suspect 集，本地 step 只填摘要。）

量比预期（代表臂，含 TT W*）：

```json
{
  "summary_bytes_per_rank": 180,
  "detail_phase_bytes_per_rank": 14000,
  "detail_tt_bytes_per_rank": 2519040,
  "detail_bytes_per_rank": 2533040,
  "naive_total_bytes": 40528640,
  "federated_total_bytes": 2535920,
  "volume_ratio_federated_over_naive": 0.06257106085967849,
  "expected_saving_factor": 15.981829079781697
}
```

## 6. 支撑设计决策

联邦过滤 principle = **健康机不回传明细、只回「我正常」摘要**；
阈值全部复用已标定 ①-A/①-B，FPR 预算与 ①-A loud 对齐；
明细门走协调 suspect（避免注入下全员 step 升高导致无去噪）。

