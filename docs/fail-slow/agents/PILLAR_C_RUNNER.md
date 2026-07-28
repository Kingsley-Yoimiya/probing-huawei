# 任务卡 · Pillar-C Runner（E1–E4 · 数据量小）

> **方案真相源**：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md` §2–§3。  
> **队列**：[`../PILLAR_C_QUEUE.md`](../PILLAR_C_QUEUE.md)。  
> **门禁**：[`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) GATE + **§2.0 机制三缺口（C0）**。

## 定位（勿跑偏）

- C 证的是 trade-off 的**「数据量小」腿**：同覆盖下总数据量 vs 全量精采。
- **不是**旧版「三臂 cold MiB 比」；**不是**扫 `COLD_MAX` 主线；**不是**判训练 step_ms 的 D。
- 旧 `pillar_c/*/VOLUME_RATIO.md` → **SUPERSEDED**（见队列 §0）。

## 硬前置

1. GATE.md G1–G6 绿（已有）。
2. **C0**：`MECH_FIX.md` 证明 SET→live tracer / `rate=0` / 窗语义可用；否则只做 E1-off，不上 E2–E4 终态。
3. `source …/env.sh`；SYY；**yysong-w0** IDLE（主池）；备选才 grj-w0；`POD_BUNDLE`/`POD_RESULTS`；让路纪律。

## 四个实验（每个产出设计参数）

| ID | 测什么 | 怎么测 | 产出 |
|----|--------|--------|------|
| **E1** | 追溯窗多大够用 | 极稀常驻 + 触发后只留最近 W 步详采；扫 W | 够用最小窗 **W\*** |
| **E2** | 平时多稀能触发 | 扫 rate=0/0.001/0.01/0.05 | 够触发最稀常驻率 |
| **E3** | 同覆盖总量比（头条） | 动态臂(W\*+最佳 rate) vs 全量臂(rate=1.0) | **动态/全量总落盘 = X%** |
| **E4** | 朴素砍量反例 | 动态臂去掉「触发升详」 | 预期归因掉级 |

另：**S1 中途接入**（outline 场景一）独立时间维；场景二并入 E2/E3；场景三 COLD_MAX **不作主线**。

## 尺与判分（生死线）

| 要 | 不要 |
|----|------|
| 数据量 = **总落盘**（全表；可分表拆解） | 只报 cold `*.memc` |
| D 差来自**该臂采集内容**能否支撑归因证据 | 用 `score_dlevel_offline` 对训练埋点判三臂（必同 D） |
| 全量臂只作数据量上界；**step_ms 不与其它臂并比** | 拿全量臂 step 慢当「检测差」 |
| 阴性 P1-EXT-A：动态应≈全量 | 修好 SET 前强行宣称阴性过 |

## 代表 case

P3-SW-A/B、P1-SW-C、P1-HW-B、P1-EXT-A（阴性）。复用 B Loud 金标覆盖，不重判训练 D。

## 资源

- 默认 **`yysong-worker-0`**（主池）；备选 `grj-megatron-32card-0716-worker-0`（IDLE + 让路）
- 落盘 `…/results/ascend-ais/pillar_c_v3/<run_id>/`（v2 目录只读归档）
- 禁止写宋盘 / 对方盘 / 删 yysong·grj vcjob；grj 对方训练再现让路

## 产出

| 产物 | 路径 |
|------|------|
| 每 PR / E 的 SUMMARY + 参数表 | `pillar_c_v3/<id>/` |
| PR-1 验收 | `pillar_c_v3/pr1_verify/` |
| 队列/ledger 回填 | 手册 + ledger 变更记录 |
| 机制修复说明 | `_prep/pillar_c_gate/MECH_FIX.md`（若沿用） |

## 派发骨架

```text
你是 Pillar-C Runner（v3）。必读 PILLAR-C-V3-EXECUTION-HANDBOOK.md、RESOURCE.md、本卡。
主池 hold=yysong-w0；备选 grj-w0（IDLE+让路）。POD_BUNDLE/POD_RESULTS 见 env.sh。
产物 pillar_c_v3/。派遣 model=composer-2.5。守 AFS 前缀与禁止项。
```
