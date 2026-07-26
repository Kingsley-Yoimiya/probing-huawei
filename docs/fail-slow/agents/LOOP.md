# Loop 状态机 · Pillar C v2（E1–E4 · **主线已收官 2026-07-26**）

> 摘要：`results/ascend-ais/pillar_c_v2/CAMPAIGN_SUMMARY.md`。默认不再派新格；补测须用户明示。

> 用户用 Cursor `/loop` 跑本状态机。  
> **方案真相源**：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md`。  
> 队列：[`../PILLAR_C_QUEUE.md`](../PILLAR_C_QUEUE.md) · 任务卡：[`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md)。  
> Dose Sweep 代表集+扩展集已收官；本波 **C 为主**。Dose 残留（P2-SW-B/C）仅空闲时顺手，不挡 C。

## 目标

证「数据量小」腿：**同覆盖下总落盘字节**（动态刷入 vs 全量精采）。主线 = **C0 → E1-off → E1 → E2 → E3 → E4(+S1)**。

旧 `pillar_c/*/VOLUME_RATIO.md`（cold 三臂）= **SUPERSEDED**，不作终态。

## 每轮必读

1. [`RESOURCE.md`](RESOURCE.md)  
2. [`../PILLAR_C_QUEUE.md`](../PILLAR_C_QUEUE.md)  
3. [`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md) / [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md)  
4. [`../ledger.md`](../ledger.md) §4.1 + 变更记录首行  
5. `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST.md`  
6. `$LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/{GATE,MECH_FIX}.md`

## 资源（硬）

| 池 | Pod | 同时刻 |
|----|-----|--------|
| Pillar C | `grj-megatron-32card-0716-worker-0` | ≤1 formal / 短测 |
| C0 短测 / 备用 | `grj-megatron-32card-0716-master-0`（IDLE 时） | ≤1 短 |
| Loop 父 | — | 0 卡 |

```bash
export POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
export POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
# 产物目录：…/pillar_c_v2/<run_id>/（勿写旧 pillar_c/ 当终态）
```

**让路**：grj 出现对方训练 → 停我方；不删对方 vcjob；不写 geruijun / 宋盘。

## 开跑前接线

| # | 项 |
|---|-----|
| W3 | GATE G1–G6 已绿（机制旋钮名） |
| W3b | **C0 / MECH_FIX**：SET→live tracer、`rate=0`、窗语义（或标定） |
| W4 | grj-w0 IDLE + POD_* 正确 |

> GATE G3=Y 只证「配置可读回」；**不等于** live tracer 已升详。E2+ 必须以 MECH_FIX 为准。

## 每轮决策

```text
读 PILLAR_C_QUEUE + MECH_FIX + LOOP_LAST + grj-w0 IDLE（让路检查）

if C0 未绿:
  派 Code/C0（本地修 → 短测@grj-w0）写 MECH_FIX.md
  可并行派 E1-off（离线截窗，不占卡）
elif E1-off 未出初版 W*:
  派 E1-off
elif grj-w0 IDLE:
  按队列下一格派 E1 → E2 → E3 → E4/S1（产物 pillar_c_v2/）
  主尺=总落盘；判分=采集归因；禁止只报 cold / 禁止训练 step_ms 判三臂同 D

写 LOOP_LAST；中文短报；父不自己开训
```

## LOOP_LAST 模板

```markdown
# LOOP_LAST
time: …
campaign: pillar_c_v2
pools: c=grj-w0 | spare=grj-m0
wire: W3=GATE_ok | W3b=C0_… | W4=…
queue: C0=… E1-off=… E1=… E2=… E3=… E4=…
grj_yield: no|yes+reason
blockers: …
next_round: …
```

## 战役成功

- C0 三缺口有 MECH_FIX 证据  
- E1 定 W\*；E2 定常驻率；E3 出「动态/全量=X%」头条；E4 反例成立  
- 阴性 P1-EXT-A 在修好 SET 后重测  
- 无写对方盘、无碰 a3；关键产物回拉本机 `results/ascend-ais/pillar_c_v2/`
