# 参数标定队列（Param-Calib · 2026-07-26 开）

> **方案真相源**：`project/reading-paper/writing/probing-paper/PILLAR-C-PARAM-CALIBRATION-PLAN.md`（4 模块 × 每参数一实验）。
> **任务卡**：[`agents/PARAM_CALIB_RUNNER.md`](agents/PARAM_CALIB_RUNNER.md)。
> **和 Pillar-C v2 的关系**：Pillar-C v2 证「数据量小」头条（E1–E4/S1）；本队列**往下钻一层**——把每个模块的**参数**用数据标定出来，回答"为什么这么设"。
> **纪律**：每个实验**单自变量**，其余全固定；**ground truth**：C0 健康线定 FPR、C1/C2 定召回/D-level。禁止拿训练 step_ms 判采集差异。

## 0. 为什么要这个队列

Pillar-C v2 的头条数字（W\*、动态/全量%）**背后的参数目前多是拍的**（判据阈 1.3/1.15/1.05、跨 rank 1.5×、worst_fraction 0.25、追溯窗、升精度 rate…）。本队列用**控制变量实验**把每个参数的值**从数据推出来**，让论文能写"每个设计参数都由实验确定、改动它会怎样我们测过"。

## 1. 硬前置（复用 Pillar-C 的 C0 机制门禁）

| ID | 缺口 | 状态 | 阻塞哪批 |
|----|------|------|---------|
| C0-a | SET→live tracer 回调桥 | ✅ PASS（`c0_mech_20260726_172201`） | 批次2/3 |
| C0-b | `rate=0` 合法 | ✅ PASS | 批次2 |
| **P-FIX** | **SET 真相键** `probing.torch.profiling=`（✅）+ 关键小表(cpu.util)环调大 + 注入尖刺标定够 | ✅ **DONE**（`p_fix_20260727_003642`：环 8MiB/span≈36s；尖刺 top=0.618s n=26；见 `_prep/pillar_c_gate/P_FIX.md`） | **批次2 可开** |

> **批次1 不依赖任何机制修复**（纯离线读现有 jsonl）——可立即开跑。

## 2. 数据地基（本机已备，批次1 离线够用）

三线（C0/C1/C2）× 三档 run，本机齐全：

| case | loud | quiet | masked | 用途 |
|---|---|---|---|---|
| **P3-EXT-A** | `20260725_001251` | `20260726_075912` | `20260726_094648` | **①-A 档阈曲线黄金数据（三档齐）** |
| **P3-SW-C** | `20260725_135238` | `20260726_125953` | `20260726_135016` | **①-A 三档齐**（勿用 stub `132433`） |
| P3-SW-A | `20260725_012957` | — | — | ①-B 定位阈 / ②-A 追溯窗 |
| P1-EXT-A | `20260725_011129` | — | `20260726_...` | ①-B / 阴性 |
| P1-SW-C | `20260725_121105` | — | — | ②-A W\*（唯一 v2 成功过） |
| P1-EXT-B / P3-EXT-B/C / P1-SW-A/B / P2-SW-B/C / P3-SW-B | loud（部分含弱档） | | | ①-A 分母扩充 |

字段齐全：`step_ms/compute_ms/comm_ms/wait_ms/data_ms/ts` + 16 rank。

## 3. 实验队列（按批次）

### 批次 1 · 纯离线（本机数据够，最先跑，不占集群）

| ID | 内容 | 输出参数 | 数据来源 | 状态 |
|----|------|---------|---------|------|
| **①-A 档阈曲线** | 扫判据阈 θ=1.02→1.5；C0 定 FPR、C1 定召回 | 每档 θ\*（loud/quiet/masked）+ FPR–召回 vs θ 曲线 | P3-EXT-A、P3-SW-C 三档齐 + 其余 loud | ✅ **DONE** `1A_dose_threshold` → θ\*=**1.16/1.12/1.04**（B=1%/5%/12%；旧 1.3/1.15/1.05 邻近）；`param_calib/1A_dose_threshold/{PARAM.json,PARAM.md}` |
| **①-B 定位阈** | 扫跨 rank max/min 阈 1.2→2.0；worst_fraction 附扫 | θ\*=**1.2** / φ\*=**0.4**（主池 P1+P3-SW-A；GPU FPR=0；旧 1.5/0.25） | C1/C2 全 16 rank（victim=7） | ✅ **DONE** `1B_localize_threshold` → `param_calib/1B_localize_threshold/{PARAM.json,PARAM.md}` |
| **②-B 环容量换算** | 容量 5/10/20/40MB ↔ 能留多少步 | 默认 **10 MB**（满环≈407 步≈4×W\*）；20MB 满环≈**814**（v2「546」=67% fill 观测跨度） | full_fidelity `python.torch_trace` MEMT（B/step≈24.6KiB） | ✅ **DONE** `2B_ring_capacity` → `param_calib/2B_ring_capacity/{PARAM.json,PARAM.md}` |

### 批次 2 · 需先修 P-fix（SET 键 + 关键表环 + 尖刺）

| ID | 内容 | 输出参数 | 集群 | 状态 |
|----|------|---------|------|------|
| **②-A W\*** | 扫追溯窗 W=10..全程，判够不够归因 D4；4 case 都要出 | W\*=**100**（max；P1-HW-B/P1-SW-C=**100**；P3-SW-A/B=**10**） | grj-w0 | ✅ **DONE** `2A_trace_window` → `param_calib/2A_trace_window/{PARAM.json,PARAM.md}` |
| **③-A 升精度增益** | 触发后 rate=0.001→1.0，判 D-level | rate\*=**0.001**（E4=D2 必要性锚；≥0.001 均 D4；TT 饱和 81552） | grj-w0 | ✅ **DONE** `3A_upgrade_rate` → `param_calib/3A_upgrade_rate/{PARAM.json,PARAM.md}`（parent=`014151`；SET_SCOPE=victim） |
| **③-B 升精度延迟** | 测 SET 生效 step 数 | SET→够TT 上界≤**12**（<<150；机制≈1；W*本轮无效） | grj-w0 | ✅ **DONE** `3B_upgrade_latency` → `param_calib/3B_upgrade_latency/{PARAM.json,PARAM.md}`（parent=`023223`；SET_SCOPE=victim；rate=1.0） |

### 批次 3 · 需先设计健康机摘要判据

| ID | 内容 | 输出参数 | 集群 | 状态 |
|----|------|---------|------|------|
| **判据** 健康机摘要 | 什么算健康 / 回传摘要 schema / FPR 预算 | 可执行 CRITERIA | 离线 | ✅ **判据 LOCKED** → `param_calib/4_health_summary_criteria/{CRITERIA.json,CRITERIA.md}`（θ*①-A + 跨rank/φ①-B；C0 FPR(loud)=0≤1%；victim∈suspects 召回=1） |
| **④-A 去噪量** | 朴素全聚 vs 联邦过滤聚 | 回传数据量比 + 定位时间 | 多机 | ✅ **DONE** `4A_federated_denoise` → volume_ratio≈**0.0626**（~16×）；localize≈**2.84 ms**（离线 harness）；`param_calib/4A_federated_denoise/{PARAM.json,PARAM.md}` |
| **④-B 聚合延迟** | 扫 rank 数 8/16/32/64 | 设计切换 **N≥17→Coordinator**（≤16 Node；延迟交叉@8）；N=64 coord≈21.5ms / peers 63→3 | 仿真+本机HTTP校准 | ✅ **DONE** `4B_fanout_latency` → mode=`simulated_network+local_http_calib`；`param_calib/4B_fanout_latency/{PARAM.json,PARAM.md}` |

### 批次 4 · 补充（结合②③）

| ID | 内容 | 状态 |
|----|------|------|
| ②-C 本地不聚 vs 常驻预聚 | ✅ **DONE** `2C_local_vs_preagg` → policy=**local_retain_trigger_then_aggregate**（preagg=off）；500步×每步 SUMMARY 开销≈**1.63 MiB / 3.38 s**（相对训墙钟 **4.12%**），常驻收益=**0**；`param_calib/2C_local_vs_preagg/{PARAM.json,PARAM.md}` |
| ③-C 局部升 vs 全局升 | ✅ **DONE** `3C_local_vs_global_upgrade` → scope=**local_suspect_only**；量比 local/global=**0.0625**（16×）；D4=D4 同级；复用 ③-A `014151`+离线外推（避 all-SET 死锁）；`param_calib/3C_local_vs_global_upgrade/{PARAM.json,PARAM.md}`；**批次4收官** |

## 4. 资源

- hold：`grj-megatron-32card-0716-worker-0`（默认；m0 IDLE 可借）
- 批次1 **不占卡**（本地 python 读 jsonl）
- `POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`
- `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`
- 产物：`results/ascend-ais/param_calib/<exp_id>/`（与 pillar_c_v2 分目录）

## 5. 执行顺序

```text
批次1（①-A/①-B/②-B，纯离线，先出）
  → P-fix（修 SET 键 + 关键表环 + 尖刺标定）
  → 批次2（②-A/③-A/③-B）
  → 设计健康摘要判据 → 批次3（④-A/④-B）
  → 批次4（②-C/③-C）
```

**①-A 档阈曲线最先跑**——本机数据够、最能证"参数由数据定而非拍"。

## 6. 每格产出的统一记录（喂论文）

每个实验产出落 `results/ascend-ais/param_calib/<exp_id>/`：
- `PARAM.json`：`{param, swept_range, chosen_value, ground_truth_source, supports_design}`
- `PARAM.md`：曲线/表 + 一句"这数据证明为什么这么设"
- 回填本队列状态 + ledger §4.1 一行

## 7. 诚实

- v2 的 E1/E3 数字**不作数**（W\* 未标定、量比几乎没省），本队列是把参数做扎实。
- 批次1–4 已齐（①–④ + ②-C/③-C）；批次3 判据 LOCKED、④-B=`simulated_network+local_http_calib`（无伪造 64 卡 live）。
- **批次4 收官**（②-C + ③-C）→ **Param-Calib 主队列可收官**。
- 不改正在跑的 pillar_c_v2 文件；本队列另起 `param_calib/` 目录。
