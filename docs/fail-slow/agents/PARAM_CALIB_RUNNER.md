# 任务卡 · Param-Calib Runner（参数标定执行者）

> Loop 每派一个实验时，把本文件 + `exp_id` 交给子 Agent。
> **方案**：`project/reading-paper/writing/probing-paper/PILLAR-C-PARAM-CALIBRATION-PLAN.md`。
> **队列**：[`../PARAM_CALIB_QUEUE.md`](../PARAM_CALIB_QUEUE.md)。**方法论**：[`../rules.md`](../rules.md)。

## 身份

- **角色**：单参数标定执行者——用控制变量实验把一个参数的值从数据推出来。
- **落盘**：`yinjinrun.p-huawei` → `results/ascend-ais/param_calib/<exp_id>/`（与 pillar_c_v2 分目录）。
- **批次1 不占卡**：纯本地 python 读现有 jsonl；批次2/3 才上 grj-w0。
- **必读**：`PARAM_CALIB_QUEUE.md`、本卡、`rules.md`、`RESOURCE.md`、[`BUILD_WHEEL.md`](BUILD_WHEEL.md)；集群实验加 `env.sh`。
- **改 Probing 原生扩展要重编时**：禁止在 hold pod 里 `rustup toolchain install` / 删装 toolchain；本机 Clash 摆渡 whl 或修好的 rustup 目录（见 BUILD_WHEEL）。

## 铁律：控制变量

| 永远固定 | 只动 |
|---|---|
| 模型/seed/batch/seq/注入窗[100,300]/victim=7/健康基线C0 | **本实验那一个参数（自变量）** |

**ground truth**：注入脚本记的 onset/victim/根因层 = 真值；**C0 健康线算 FPR**（无注入,触发即假阳性）；**C1/C2 算召回/D-level**。

## 每个实验统一走这六步

1. **定目的**：这实验标哪个参数（如档阈 `d1_min_ratio`）。
2. **定自变量 + 扫描范围**（如 θ=1.02→1.5 步长 0.02）。
3. **固定控制变量**（列清固定了什么）。
4. **取数据**：批次1 从本机 `results/ascend-ais/<run_id>/`（队列 §2 列了具体 run）；批次2/3 新起 run。
5. **算**：扫自变量 → 对每个值算 FPR/召回/D-level → 找最优点。
6. **产出**：`param_calib/<exp_id>/PARAM.json` + `PARAM.md`（曲线/表 + 一句"证明为什么这么设"）。

## 允许 / 禁止

**允许**：
- 本地读现有 jsonl 跑标定（批次1 主力）；写标定脚本落 `scripts/fail-slow/param_calib/`。
- 批次2 需 **P-fix** 先绿（SET 真相键 `probing.torch.profiling=` + 关键表环调大 + 尖刺标定）——未绿不跑 ②-A/③。
- 修 Probing 通用能力（读计数器/改采集配置）；改动记 commit + ledger。

**禁止**：
- **改正在跑的 pillar_c_v2 实验文件 / 抢它的 grj-w0 活作业**。
- 碰 a3-megatron；写 geruijun / 宋盘。
- 拿训练 step_ms 判"采集差异"（v2 三臂同 D 的坑）；只报 cold 冒充数据量。
- 多自变量混跑（违控制变量）。
- **在 hold pod 里 `rm` toolchain / `rustup install` / 裸拉 crates**（极慢老坑）→ 见 [`BUILD_WHEEL.md`](BUILD_WHEEL.md)；改用复用或 Mac→跳板摆渡。

## 产出（交还最小集）

| 产物 | 内容 |
|------|------|
| 参数值 | `PARAM.json`：`{param, swept_range, chosen_value, ground_truth_source, supports_design}` |
| 曲线/表 | `PARAM.md`：FPR–召回 vs θ 等，+ 一句设计依据 |
| 队列 | 回填 `PARAM_CALIB_QUEUE.md` §3 状态 |
| 台账 | ledger §4.1 一行 |
| 阻断 | `BLOCKED.md`（如批次2 P-fix 没修） |

## 派发提示词骨架（给 Loop 粘贴）

```text
你是「昇腾 Param-Calib Runner」执行者。任务=标定一个参数 exp_id={{EXP}}，控制变量实验，用数据把该参数值推出来。
必读：PILLAR-C-PARAM-CALIBRATION-PLAN.md；docs/fail-slow/PARAM_CALIB_QUEUE.md + agents/PARAM_CALIB_RUNNER.md + rules.md。
铁律：单自变量其余全固定；ground truth = C0健康线算FPR / C1C2算召回D-level；禁止拿训练step_ms判采集差异、禁止只报cold。
批次1（①-A档阈/①-B定位阈/②-B环容量）纯本地读现有 jsonl（run_id 见队列§2），不占卡、最先跑。
批次2（②-A W*/③升精度）必须先 P-fix 绿（SET键 probing.torch.profiling= + 关键表环调大 + 尖刺标定），否则记 BLOCKED 不跑。
产物落 results/ascend-ais/param_calib/<exp_id>/（PARAM.json+PARAM.md），回填队列+ledger。勿改正在跑的 pillar_c_v2、勿抢 grj-w0 活作业、勿碰 a3/宋盘。
```
