# Loop 状态机 · Pillar C v3（机制修复 · 按诊断时间线）

> **现役**（2026-07-27）。手册已按故障时间线重排 PR。  
> 施工手册：`project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md`  
> 复制提示词：[`LOOP_PROMPT.md`](LOOP_PROMPT.md)  
> **派遣**：Task = **composer-2.5**（非 fast）

## 推进原则

1. **先改代码，再上机验收**  
2. 改代码同时可并行：**附录 A 离线消融**、本机/短 smoke  
3. 最小闭环 = **PR-1 + PR-2**；PR-3/4 = stretch  
4. 多阶段：A（常驻地基）→ B（定位+SET）→ C（回查/多机）

## 每轮必读

1. [`RESOURCE.md`](RESOURCE.md)  
2. 手册最新版  
3. `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST.md`

## 资源

| 池 | Pod | 用途 |
|----|-----|------|
| Pillar C | **yysong-worker-0** | PR 验收长跑 / 正式实验 |
| 短测 | yysong-master-0 | smoke |
| 备选 | grj-* | 仅 IDLE+让路，先汇报 |

写盘：AFS `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v3/`  
本机：`project/probing-huawei/results/ascend-ais/pillar_c_v3/`

## 每轮决策

```text
读手册 + LOOP_LAST + yysong-w0 IDLE

阶段 A:
  if PR-1 code 未齐 → 派 composer-2.5 改代码（可并行附录 A）
  elif PR-1 baseline 未过 → 派健康长 run @yysong-w0
阶段 B:
  elif PR-2 code 未齐 → 派编排定位 SQL + SET 键名
  elif PR-2 实验未齐 → 按手册 §2.4 A→B→C
阶段 C (stretch):
  elif 有机时 → PR-3 / PR-4
else → 写 LOOP_LAST 等指示

父不自己开训；关键产物立刻回拉
```

## LOOP_LAST 模板

```markdown
# LOOP_LAST
time: …
campaign: pillar_c_v3
pools: c=yysong-w0
dispatch_model: composer-2.5
phase: A|B|C
queue: PR1_code=… PR1_baseline=… ablation=… PR2_code=… PR2_exp=… PR3=… PR4=…
blockers: …
next_round: …
```
