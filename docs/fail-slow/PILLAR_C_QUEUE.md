# Pillar-C 队列（E1–E4 · 2026-07-26 重开）

> **方案真相源**：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md`（§2 E1–E4）。  
> **任务卡**：[`agents/PILLAR_C_RUNNER.md`](agents/PILLAR_C_RUNNER.md) · [`agents/PILLAR_C_PILOT.md`](agents/PILLAR_C_PILOT.md)。  
> **纪律**：C 主尺=**同覆盖下总落盘字节比**（禁止只报 cold）；判分=**采集内容够不够归因**（禁止拿训练 step_ms 判三臂同 D）。

## 0. 上一版作废（勿当终态）

| 旧产物 | 问题 | 处置 |
|--------|------|------|
| `pillar_c/*/VOLUME_RATIO.md`（三臂 cold MiB） | 尺用错：cold≈总量 10%；三臂同训→offline 同 D | **SUPERSEDED**；可留作机制摸底 |
| C-2 `COLD_MAX` 扫 | 计划 §3 已降级/废弃；抬预算未抬冷量 | **附录可选**，不作主线 |
| C-3 mid_set 短窗 | SET 配置落盘≠live tracer 生效（§2.0）；终冷≈C-2 | **机制证据保留**；主线改走 E2/E3 |
| P1-EXT-A NEGATIVE_FAIL | SET 未真正升详 + SAMPLE_MS 主导 | 修好 §2.0 后重测阴性 |

## 1. 硬前置（§2.0 代码门禁）

| ID | 缺口 | 状态 | 阻塞 |
|----|------|------|------|
| C0-a | SET→live tracer 回调桥（勿 Tokio 内 `configure()`） | ✅ PASS @grj-w0（TT 29→309） | E2/E3/E4 |
| C0-b | `rate=0` 合法（平时不写 torch_trace） | ✅ PASS @grj-w0（trace=0 / timing=28） | E1/E2 |
| C0-c | 追溯窗按步/按秒（或标定 20MB≈步） | 🟡 E1-off 标定 20MB≈546 步；正式 E1=`offline_truncate`（无 online retention API） | E3 可选 online |

> C0-a/b 已绿 → 可开 E1/E2。C0-c（窗标定）走 E1-off，不挡扫 rate。未绿前仍**禁止**宣称「动态刷入臂达同 D4」。

## 2. 实验队列

| ID | 内容 | 产出 | 集群？ | 状态 |
|----|------|------|--------|------|
| **C0** | 修 §2.0 三缺口 + 短测 | commit + `pillar_c_gate/MECH_FIX.md` | 本地→短测@grj-w0 | ✅ C0-a/b PASS（`c0_mech_20260726_172201`）；C0-c=E1-off |
| **E1-off** | 已有 full run torch_trace **离线截窗**重判 D | W\* 初版表 | 否 | ✅ DONE：`W_STAR.md`；P1-SW-C **W\*=100**；P3 UNRESOLVED；P1-HW-B NO_W_STAR |
| **E1** | 扫 W=50/100/200；P1-SW-C | 够用最小窗 W\* | grj-m0 | ✅ 收口 `173830`：`offline_truncate` **NO_W_STAR**（未复现 off=100）；`173220` INVALID PATH；设计 W\*仍用 E1-off=**100** |
| **E2** | 扫常驻 rate=0/0.001/0.01/0.05 | 够触发最稀率 | grj-w0 | ✅ BOUNDARY **0**（`173134`；0&0.05 RSS ok；中间跳过） |
| **E3** | 动态臂(W\*+最佳 rate) vs 全量臂；**总落盘** | 动态/全量=X% | grj-w0 | ✅ `181423` P3-SW-A：**72.6%**（W\*=100 content est；raw=90.16%）；SET_OK；RSS 同覆盖 |
| **E4** | 砍量臂=动态去触发升详 | 预期掉级 | grj-w0 | ✅ `182630` P3-SW-A：**PASS 掉级**；禁 SET（log 缺席）；TT rows 0 vs E3 54054；RSS 仍 Y（周期小表）；量≈E3 raw |
| **S1** | 中途接入回溯（outline 场景一） | 窗长 vs 重启代价 | grj-w0 | ✅ `184311` P3-SW-A：**PASS_ATTACH_NO_PRE_ONSET**；attach@150（onset 后）RSS Y；onset 前 n=0；热接入 restart=0 vs 对手≈150 步；延迟 site_hook（无 libprobing.so） |

代表 case（§2.3）：**P3-SW-A/B、P1-SW-C、P1-HW-B、P1-EXT-A（阴性）**。复用 B Loud 金标，不重判训练 D。

## 3. 资源

- hold：`grj-megatron-32card-0716-worker-0`（Dose 已收官；m0 可借用但默认 w0）
- `POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`
- `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`
- 产物：`results/ascend-ais/pillar_c_v2/<run_id>/`（与旧 `pillar_c/` 分目录）

## 4. 执行顺序

```text
C0 代码绿 → E1-off（可与 C0 并行）→ E1 → E2 → E3 头条 → E4 + S1
```

## 5. 收官（2026-07-26）

主线 **C0 → E1-off → E1 → E2 → E3 → E4 → S1** 已全绿。战役摘要：[`../../results/ascend-ais/pillar_c_v2/CAMPAIGN_SUMMARY.md`](../../results/ascend-ais/pillar_c_v2/CAMPAIGN_SUMMARY.md)。
