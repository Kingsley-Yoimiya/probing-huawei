## 现役战役（2026-07-27 起）：Pillar C v3

| 流水线 | 目标 | 落点 | 谁跑 |
|--------|------|------|------|
| **Pillar C v3** | 手册 PR-1→PR-3（机制修复 + 重跑） | **yysong-w0** + `pillar_c_v3/` | Runner；Task=**composer-2.5** |
| Pillar C v2 | 已收官 | `pillar_c_v2/` | 只读归档 |
| Dose（收官） | 代表+扩展 Quiet/Masked 已齐 | DOSE_QUEUE | 仅残留顺手 |

- 施工手册：`project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md`  
- 资源主池：**yysong**（64）；grj 仅 IDLE 备选 — [`RESOURCE.md`](RESOURCE.md)

## 文件

| 文件 | 角色 |
|------|------|
| [`LOOP.md`](LOOP.md) / [`LOOP_PROMPT.md`](LOOP_PROMPT.md) | **现役** C v3 状态机 + 复制提示词 |
| [`PILLAR_C_RUNNER.md`](PILLAR_C_RUNNER.md) | 正式采集 / PR 验收 |
| [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) | v2 门禁（归档参考） |
| [`RESOURCE.md`](RESOURCE.md) | 机器池（yysong 主 / grj 备） |
| [`BUILD_WHEEL.md`](BUILD_WHEEL.md) | 编/装 probing wheel 铁律 |
| 其余 Dose/Loud 卡 | 归档或顺手 |

## Loop 一句话

每轮：**(1)** yysong-w0 IDLE？**(2)** 手册下一 PR/实验？**(3)** Task(composer-2.5) 派发；**(4)** LOOP_LAST + 回拉。
