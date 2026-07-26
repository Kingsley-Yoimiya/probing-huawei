# ①-B 定位阈标定 · 跨 rank max/min + worst_fraction（exp=`1B_localize_threshold`）

> 主自变量=跨 rank θ∈[1.2,2.0]；`worst_fraction` 为**次扫**（θ 已定后附扫）。
> 档阈固定 ①-A：loud=1.16 / quiet=1.12 / masked=1.04（dose 门控）。
> 窗 `[100,300]`；真值 victim=7；C0→FPR，C1/C2→定位召回/误指。
> 度量：P1 GPU=`compute_ms` 取 **min**（victim compute 偏低）；P3=`data_ms` 取 **max**（与 offline D3 同族）。
> 禁止 cold / 训练 step_ms「采集差异」叙事。

## 为什么这么设（一句）

**θ\*=1.2，φ\*=0.4**：在 dose 门控后、**可定位到 victim** 的 C1/C2 全 16 rank 上扫跨 rank max/min（P1=`compute_ms/min`，P3-SW-A=`data_ms/max`），取准确率 vs 误指率交叉或 Youden；host-wide（P3-EXT/P3-SW-C）整机争用另表（exact rank 不可比）。旧默认 1.5 / 0.25 落在邻近；masked 弱档 ratio 常 <1.2，靠相位极端值而非高阈。

## 控制变量

| 固定 | 值 |
|---|---|
| 窗 | `[100,300]` |
| victim | rank 7 |
| 档阈（①-A） | loud 1.16 / quiet 1.12 / masked 1.04 |
| 主自变量 | 跨 rank max/min θ ∈ [1.2, 2.0] step 0.05 |
| 次扫 | worst_fraction φ ∈ [0.05, 0.50] step 0.05（θ 不参与） |
| 主标定池 | P1-* + P3-SW-A（可 exact 指到 r7） |
| 排除主表 | P3-EXT / P3-SW-C（host-wide；D3=same_host） |

## 推荐参数

| 参数 | 值 | hit | mis | C0 FPR | 旧默认 | 选点 |
|---|---:|---:|---:|---:|---:|---|
| 跨 rank θ\* | **1.2** | 0.889 | 0.000 | 0.333 | 1.5 | Youden=argmax(hit_rate−mispoint_rate)（无清晰交叉时） |
| worst_fraction φ\* | **0.4** | 1.000 | 0.000 | 0.222 | 0.25 | max Youden s.t. C0 FPR≤30%（附扫；控健康误报） |

## 试验明细（主池 C1/C2；窗中位）

| case | dose | arm | role | metric | ratio | pred | hit | dose_gate | wf7 | wf_pred |
|---|---|---|---|---|---:|---:|---|---|---:|---:|
| P3-SW-A | loud | C1 | primary | data_ms/max | 332.407 | 7 | Y | Y | 0.995 | 7 |
| P3-SW-A | loud | C2 | primary | data_ms/max | 341.181 | 7 | Y | Y | 0.995 | 7 |
| P1-EXT-A | loud | C1 | primary | compute_ms/min | 4.374 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-A | loud | C2 | primary | compute_ms/min | 4.359 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-A | masked | C1 | primary | compute_ms/min | 1.105 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-A | masked | C2 | primary | compute_ms/min | 1.107 | 7 | Y | Y | 0.965 | 7 |
| P1-EXT-A | quiet | C1 | expand | compute_ms/min | 1.205 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-A | quiet | C2 | expand | compute_ms/min | 1.208 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-B | loud | C1 | expand | compute_ms/min | 2.223 | 7 | Y | Y | 0.985 | 7 |
| P1-EXT-B | loud | C2 | expand | compute_ms/min | 2.233 | 7 | Y | Y | 0.985 | 7 |
| P1-SW-A | loud | C1 | expand | compute_ms/min | 4.766 | 7 | Y | Y | 0.662 | 7 |
| P1-SW-A | loud | C2 | expand | compute_ms/min | 4.756 | 7 | Y | Y | 0.662 | 7 |
| P1-HW-B | loud | C1 | expand | compute_ms/min | 1.713 | 7 | Y | Y | 0.985 | 7 |
| P1-HW-B | loud | C2 | expand | compute_ms/min | 1.712 | 7 | Y | Y | 0.980 | 7 |
| P3-SW-A | quiet | C1 | expand | data_ms/max | 238.684 | 7 | Y | Y | 0.995 | 7 |
| P3-SW-A | quiet | C2 | expand | data_ms/max | 282.022 | 7 | Y | Y | 0.995 | 7 |
| P3-SW-A | masked | C1 | expand | data_ms/max | 199.003 | 7 | Y | Y | 0.995 | 7 |
| P3-SW-A | masked | C2 | expand | data_ms/max | 166.141 | 7 | Y | Y | 0.995 | 7 |

## θ 扫描（主池；exact hit）

| θ | hit_rate | mispoint | miss | precision | C0 FPR | Youden |
|---:|---:|---:|---:|---:|---:|---:|
| 1.20 | 0.889 | 0.000 | 0.111 | 1.000 | 0.333 | 0.889 ←θ\* |
| 1.25 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.30 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.35 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.40 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.45 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.50 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.55 | 0.778 | 0.000 | 0.222 | 1.000 | 0.333 | 0.778 |
| 1.60 | 0.778 | 0.000 | 0.222 | 1.000 | 0.222 | 0.778 |
| 1.65 | 0.778 | 0.000 | 0.222 | 1.000 | 0.222 | 0.778 |
| 1.70 | 0.778 | 0.000 | 0.222 | 1.000 | 0.222 | 0.778 |
| 1.75 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |
| 1.80 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |
| 1.85 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |
| 1.90 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |
| 1.95 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |
| 2.00 | 0.667 | 0.000 | 0.333 | 1.000 | 0.222 | 0.667 |

## φ 扫描（worst_fraction；附）

| φ | hit_rate | mispoint | miss | C0 FPR | Youden |
|---:|---:|---:|---:|---:|---:|
| 0.05 | 1.000 | 0.000 | 0.000 | 1.000 | 1.000 |
| 0.10 | 1.000 | 0.000 | 0.000 | 1.000 | 1.000 |
| 0.15 | 1.000 | 0.000 | 0.000 | 1.000 | 1.000 |
| 0.20 | 1.000 | 0.000 | 0.000 | 0.667 | 1.000 |
| 0.25 | 1.000 | 0.000 | 0.000 | 0.556 | 1.000 |
| 0.30 | 1.000 | 0.000 | 0.000 | 0.444 | 1.000 |
| 0.35 | 1.000 | 0.000 | 0.000 | 0.333 | 1.000 |
| 0.40 | 1.000 | 0.000 | 0.000 | 0.222 | 1.000 ←φ\* |
| 0.45 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 |
| 0.50 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 |

## 分层旁证

- **GPU `compute_ms/min`**：θ\*=1.2 (hit=0.833, mis=0.000, FPR=0.000；n_inj=12；mode=exact)
- **Host 可定位 `data_ms/max`（P3-SW-A）**：θ\*=1.2 (hit=1.000, mis=0.000, FPR=1.000；n_inj=6；mode=exact)
- **Host-wide soft same_host（P3-EXT/P3-SW-C）**：θ\*=1.2 (hit=1.000, mis=0.000, FPR=1.000；n_inj=12；mode=soft_same_host)
- **全 gated exact（含 host-wide，仅对照）**：θ\*=1.75 (hit=0.433, mis=0.333, FPR=0.467；n_inj=30；mode=exact)

### 诚实注记

- **GPU 层**：C0 max/min≈1.02，扫程内 FPR≈0；θ\* 主要由召回（弱档 quiet/masked）决定。
- **P3-SW-A data_ms**：注入后 ratio≫100、指中 r7；健康窗 data_ms 噪声也可 >1.5 → 单独用 data_ms 时 C0 FPR 偏高，定位应在 dose 门控之后。
- **Host-wide**：整机争用下 exact rank 常非 7，offline D3 用 same_host；不进 θ\* 主点。
- **Masked P1-EXT-A** ratio≈1.10 <1.2：扫程下 miss，弱档需更低跨 rank 阈或只靠 dose 门。

## 图

- `fig_localize_theta.svg`
- `fig_worst_fraction.svg`

## 复现

```bash
python3 project/probing-huawei/scripts/fail-slow/param_calib/1b_localize_threshold.py \
  --results-root project/probing-huawei/results/ascend-ais \
  --out project/probing-huawei/results/ascend-ais/param_calib/1B_localize_threshold
```
