# ④-A 朴素全聚 vs 联邦过滤聚 · DONE

> 状态：**DONE** · `4A_federated_denoise` · mode=`offline_harness` · 2026-07-27T06:34:13
> harness：`scripts/fail-slow/param_calib/4a_federated_denoise.py`

## 一句话

单 victim N=16：联邦量比中位≈0.0626（约 16.0× 节省）；victim∈suspects 召回=1.0；定位墙钟中位≈2.843 ms（离线 harness）。证明健康机只回 SUMMARY、明细门走协调 ①-B 可去噪一个量级。

## 自变量 / 控制

| 项 | 值 |
|---|---|
| 自变量 | 朴素全拉 DETAIL vs 联邦（SUMMARY→suspect→DETAIL） |
| N ranks | 16 |
| victim | 7 |
| dose / θ* | loud / 1.16 |
| ①-B θ* / φ* | 1.2 / 0.4 |
| W* / TT | 100 / include=True |
| SET 键（live 约定） | `probing.torch.profiling=` scope=victim |

## 推荐参数（本实验输出）

| 参数 | 值 |
|---|---|
| aggregation | **federated_SUMMARY_then_suspect_DETAIL** |
| volume_ratio (fed/naive) | **0.062580** |
| saving_factor | **15.98×** |
| localize_culprit_ms | **2.8428**（离线中位） |
| SUMMARY B/rank（实测均值） | **208.2** |

## 两阶段查询路径（harness）

1. **Phase-1**：全 rank 序列化 CRITERIA SUMMARY schema
2. **协调**：①-A dose 门控 + ①-B（cross max/min ∨ worst_fraction）→ suspects
3. **Phase-2**：仅 suspects 拉 DETAIL（phase 窗 [trigger−W*+1, trigger] + TT W* 字节估计）
4. **对照**：全 rank DETAIL（同窗同表）

## 汇总

- 注入臂数：10
- victim∈suspects 召回：**1.0**
- 联邦定位 hit：**1.0**
- 均值 n_suspects：**1**
- volume_ratio 中位/均/min/max：0.062580 / 0.062581 / 0.062580 / 0.062584
- localize_culprit_ms 中位/均：2.8428 / 2.9041
- 朴素定位 ms 中位：2.3453

> offline harness 墙钟=本机序列化+协调+定位 CPU 时间（中位×7）；非集群 FanoutScope 网络 RTT；live 网络墙钟留给 ④-B
>
> DETAIL TT 字节按 ②-B 估计 W*×25190.4 B/step；phase 序列字节为 json 实测

## 分臂明细

| case | arm | n_sus | victim∈ | fed_hit | volume_ratio | fed_ms | naive_ms | SUMMARY_B/r |
|---|---|---:|---|---|---:|---:|---:|---:|
| P3-SW-A | C1 | 1 | Y | Y | 0.062584 | 2.6465 | 2.2631 | 205.8 |
| P3-SW-A | C2 | 1 | Y | Y | 0.062583 | 2.5546 | 2.2271 | 204.2 |
| P1-EXT-A | C1 | 1 | Y | Y | 0.062580 | 2.6764 | 2.3205 | 209.5 |
| P1-EXT-A | C2 | 1 | Y | Y | 0.062581 | 2.8086 | 2.3161 | 209.8 |
| P1-EXT-B | C1 | 1 | Y | Y | 0.062581 | 3.6713 | 2.4836 | 210.2 |
| P1-EXT-B | C2 | 1 | Y | Y | 0.062580 | 2.8445 | 2.3764 | 208.1 |
| P1-SW-A | C1 | 1 | Y | Y | 0.062580 | 3.1095 | 2.4064 | 208.5 |
| P1-SW-A | C2 | 1 | Y | Y | 0.062580 | 2.9469 | 2.4087 | 208.1 |
| P1-HW-B | C1 | 1 | Y | Y | 0.062580 | 2.9414 | 2.3659 | 208.2 |
| P1-HW-B | C2 | 1 | Y | Y | 0.062580 | 2.8412 | 2.3246 | 209.6 |

## 支撑设计决策

联邦过滤 principle = 健康机不回传明细、只回「我正常」摘要；
明细门必须走协调侧 ①-B suspects（注入下非 victim 本地 step 常升高，不能只靠本地 step）。
本实验离线正式量比证实约一个量级节省；live FanoutScope 网络延迟扫基数见 ④-B。

