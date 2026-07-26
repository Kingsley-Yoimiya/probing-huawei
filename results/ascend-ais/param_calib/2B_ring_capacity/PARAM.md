# ②-B 环容量换算 · 容量 → 保留步数（exp=`2B_ring_capacity`）

> 自变量=**环容量**（MB）；表=`python.torch_trace`（MEMT 环）。
> bytes/row = Σchunk.used / n_rows；bytes/step = Σchunk.used / n_unique(`local_step`)。
> 禁止 cold MiB / 训练 step_ms 冒充环容量结论。

## 为什么这么设（一句）

**默认环容量=10 MB**（满环≈**407** 步）：按 full 臂实测 ~24569 B/step 线性外推；相对 E1-off W\*=100（P1-SW-C，非本队列②-A）留 ≥4× 余量，比 40MB 省内存、比 5MB 更稳。v2「20MB≈546」复核为**未满环观测跨度**（fill≈67%），满环饱和约 **814** 步。

## 控制变量

| 固定 | 值 |
|---|---|
| 表 | `python.torch_trace`（MEMT v3） |
| 布局 | nchunks=8；容量=nchunks×chunk_size（SI MB） |
| 度量 | unique `local_step`；used=chunk payload 字节 |
| 自变量 | 环容量 ∈ {5, 7.5, 10, 15, 20, 30, 40} MB |
| W\* 参考（交叉，非本实验扫） | 100 ← E1-off P1-SW-C (pillar_c_v2/E1_off；非本队列②-A) |

## 推荐参数

| 参数 | 值 | 满环步数 | vs W\*=100 | 规则 |
|---|---:|---:|---:|---|
| 环容量默认 | **10 MB** | 407 | 4.1× | min C in sweep s.t. steps(C) ≥ 4×W* (400) |
| 保守（现网） | 20 MB | 814 | 8.1× | 生产默认；仍不浪费 |
| 下限慎用 | 5 MB | 203 | 2.0× | 仅约 2×W\*，升详变密易顶满 |

## 保留时长曲线（容量 → 步数）

| 容量 MB | capacity B | usable B | 可留步数 | vs W\*=100 |
|---:|---:|---:|---:|---:|
| 5 | 5000000 | 4999680 | **203** | 2.03× |
| 7.5 | 7500000 | 7499680 | **305** | 3.05× |
| 10 | 10000000 | 9999680 | **407** ←推荐 | 4.07× |
| 15 | 15000000 | 14999680 | **610** | 6.10× |
| 20 | 20000000 | 19999680 | **814** | 8.14× |
| 30 | 30000000 | 29999680 | **1221** | 12.21× |
| 40 | 40000000 | 39999680 | **1628** | 16.28× |

## 标定样本（bytes/row · bytes/step）

- 样本数：32 个 full_fidelity `python.torch_trace`（2 runs）
- 现网环：capacity=20000000 B （8×2500000）≈ **20 MB**
- 观测：steps=546（0..545），rows=79353，fill=67.1%，rows_overwritten=0
- **bytes/row** 中位 = **169.05** （mean 169.05）
- **bytes/step** 中位 = **24568.76** （≈24.0 KiB/step；rows/step≈145.3）

### 按 run

| run | n_files | steps | rows | fill% | B/step | B/row | ow |
|---|---:|---:|---:|---:|---:|---:|---:|
| `20260725_230350-pillar-c-p3-sw-a-loud` | 16 | 546 | 79353 | 67.1 | 24568.8 | 169.0 | 0 |
| `20260725_233537-pillar-c-p3-sw-b-loud` | 16 | 546 | 79353 | 67.1 | 24568.8 | 169.0 | 0 |

## 与基线 20MB≈546 对照

| 口径 | 步数 | 说明 |
|---|---:|---|
| v2 E1-off / MECH_FIX 口号 | 546 | 20MB 环内观测 unique local_step（0..545），`rows_ow=0`，**未声明满环** |
| 本实验复核（同批 full 臂） | 546 | fill≈67.1%；used=13414541 B / capacity=20000000 B |
| 本实验满环外推 @20MB | **814** | floor(usable / B_step)；修正「546=饱和」误解 |

**结论**：v2「20MB≈546」作为**某次 full run 未覆写观测跨度**成立；作为**环饱和容量**应修正为 **≈814 步**。546/814≈67%，与实测 fill 一致。

## 与 W\* 交叉（够又不浪费）

- W\*=100 来自 **E1-off P1-SW-C (pillar_c_v2/E1_off；非本队列②-A)**，本实验**不扫**追溯窗。
- 10 MB → 407 步 ≈ 4.1×W\*（够 W*=100 且不浪费；10MB≈4× 余量）。
- 40 MB ≈ 16×W\*，对单表本地环偏浪费；5 MB 仅 ~2×，升详变密时风险高。

## 方法与假设

1. 表：`python.torch_trace` MEMT 环（非 cold 目录体积）。
2. bytes/row：chunk `used` 字段之和 / 解析行数（含行长前缀，不含 40B chunk 头）。
3. bytes/step：同上 / unique `local_step`（一步多 module/stage 行）。
4. 容量扫：固定 nchunks=8，缩放 chunk_size 使 nchunks×chunk_size = C×1e6。
5. 满环步数=线性外推；本机无 `rows_overwritten>0` 样本，故饱和点无直接撞环实测。
6. 假设 full-rate 详采密度与标定臂相近；rate≪1 时同容量可留更多步。

## 图

- `fig_capacity_vs_steps.svg`：容量→步数；红虚线 W\*=100；灰点线 v2 观测 546。

## 复跑

```bash
python3 project/probing-huawei/scripts/fail-slow/param_calib/2b_ring_capacity.py \
  --results-root project/probing-huawei/results/ascend-ais \
  --out project/probing-huawei/results/ascend-ais/param_calib/2B_ring_capacity
```

