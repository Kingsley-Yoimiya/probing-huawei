# ②-C 本地保留 vs 常驻预聚 · DONE

> 状态：**DONE** · `2C_local_vs_preagg` · mode=`offline_harness_reuse_4A4B` · 2026-07-27T06:49:46
> harness：`scripts/fail-slow/param_calib/2c_local_vs_preagg.py`

## 一句话

常驻期预聚(每步 SUMMARY×16)开销 1626.6 KiB / 3.377s（相对训墙钟 4.12%），收益≈0（常驻诊断查询=0；触发后仍付④-A 现拉，Phase-1 乐观可省仅占触发延迟≈55.8%）。证明「留本地、触发才聚」省掉常驻聚合成本；聚合推迟到模块④。

## 自变量 / 控制

| 项 | 值 |
|---|---|
| 自变量 | 常驻期聚合 on/off |
| N ranks / FanoutScope | 16 / Node |
| SUMMARY B/rank（④-A） | 208.2 |
| Phase-1 ms/round（④-B Node） | 6.754 |
| 触发联邦 ms（④-B） | 12.108 |
| dose / θ* / ①-B | loud / 1.16 / 1.2·0.4 |
| W* | 100 |
| 主 run | `20260725_012957-yjr-as-c-p3-sw-a-loud` · C0 |

## 推荐参数（本实验输出）

| 参数 | 值 |
|---|---|
| resident_preagg | **off** |
| policy | **local_retain_trigger_then_aggregate** |
| 预聚开销（interval=1） | **1626.6 KiB** / **3.377 s** |
| 相对训墙钟开销 | **4.12%** |
| 常驻收益（延迟节省） | **0.000 ms**（≈0） |
| 乐观 Phase-1 可省占触发延迟 | **55.8%** |

## 常驻 horizon（现有 run）

- steps [0, 499] → **500** 步
- 稳态 step_ms 中位（C0 steps 50–99）= **164.082 ms**
- 训墙钟 ≈ **82.04 s**
- 常驻期诊断查询次数 = **0**（设计：触发才查）
- 触发查询次数 = **1**

## 两臂对照（主：interval=1）

| 臂 | 常驻字节 | 常驻墙钟 | 触发成本（④-A/B） | 常驻收益 |
|---|---:|---:|---:|---:|
| local_retain (preagg=off) | 0.0 KiB | 0.000 s | 2473.1 KiB / 12.11 ms | 0.000 ms |
| resident_preagg (on, interval=1) | 1626.6 KiB | 3.377 s | 2473.1 KiB / 12.11 ms | 0.000 ms |

## 开销 vs 预聚间隔（灵敏度；自变量仍为 on/off）

| interval | rounds | bytes (MiB) | wall (s) | vs train |
|---:|---:|---:|---:|---:|
| 1 | 500 | 1.5884 | 3.377 | 4.12% |
| 10 | 50 | 0.1588 | 0.338 | 0.41% |
| 50 | 10 | 0.0318 | 0.068 | 0.08% |
| 100 | 5 | 0.0159 | 0.034 | 0.04% |

## 收益分解

- 常驻缓存命中查询数 = **0**
- 常驻延迟节省 = **0.000 ms**
- 归因 D-level 增益 = **0.0**
- 乐观：触发时复用陈旧 Phase-1 可省 6.75 ms / 3.3 KiB（占触发延迟 **55.8%**、占触发字节 **0.13%**）
- 常驻期无诊断查询；触发后 W* DETAIL+升详必须现拉，陈旧预聚不能替代

## 长作业投影（interval=1 预聚）

| hours | steps | preagg MiB | preagg wall s | vs train | local resident |
|---:|---:|---:|---:|---:|---|
| 1 | 21940 | 69.70 | 148.2 | 4.12% | 0 B / 0 s |
| 8 | 175521 | 557.61 | 1185.4 | 4.12% | 0 B / 0 s |

## 支撑设计决策

常驻期**不必跨机聚合**：数据留各 rank 本地环（②-A/②-B），等触发后再走模块④联邦过滤聚。常驻预聚付持续 SUMMARY fan-out 成本，却换不来常驻期查询收益，也不能替代触发后的升详 DETAIL。

