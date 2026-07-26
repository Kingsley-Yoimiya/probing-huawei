## 现役战役（2026-07-26 起）：Pillar C v2（E1–E4）

| 流水线 | 目标 | 落点 | 谁跑 |
|--------|------|------|------|
| **Pillar C v2** | C0→E1–E4（同覆盖总落盘） | **grj-w0** + `pillar_c_v2/` | Runner（GATE 已绿；看 MECH_FIX） |
| Dose（收官） | 代表+扩展 Quiet/Masked 已齐 | DOSE_QUEUE | 仅残留 P2 顺手 |

- 方案：`EVAL-GAP-AND-PILLAR-C-PLAN.md`；队列：[`../PILLAR_C_QUEUE.md`](../PILLAR_C_QUEUE.md)。  
- 旧 cold 三臂 = **SUPERSEDED**。Loud → [`LOOP_LOUD.md`](LOOP_LOUD.md)。

## 文件

| 文件 | 角色 |
|------|------|
| [`LOOP.md`](LOOP.md) / [`LOOP_PROMPT.md`](LOOP_PROMPT.md) | **现役** C v2 状态机 |
| [`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md) | E1–E4 正式采集 |
| [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) | 门禁（已绿；G3≠live） |
| [`../PILLAR_C_QUEUE.md`](../PILLAR_C_QUEUE.md) | C0/E1–E4 队列 |
| [`LOOP_PARAM_CALIB.md`](LOOP_PARAM_CALIB.md) / [`PARAM_CALIB_RUNNER.md`](PARAM_CALIB_RUNNER.md) | **参数标定** loop + 任务卡（v2 往下钻一层） |
| [`../PARAM_CALIB_QUEUE.md`](../PARAM_CALIB_QUEUE.md) | 参数标定队列（批次1 离线先跑） |
| [`RESOURCE.md`](RESOURCE.md) | 机器池 |
| [`BUILD_WHEEL.md`](BUILD_WHEEL.md) | **编/装 probing wheel 铁律**（禁 pod rustup；本机 Clash 摆渡） |
| 其余 Dose/Loud 卡 | 归档或顺手 |

## Loop 一句话

每轮：**(1)** C0/MECH_FIX？**(2)** E1-off？**(3)** w0 IDLE → 下一 E 格；**(4)** 更新队列 + LOOP_LAST。主尺=总落盘。
