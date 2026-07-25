# 任务卡 · Case Runner（轨 A）

> Loop 每派发 **一个 case** 时，把本文件 + `case_id` + 档位交给子 Agent。

## 身份

- **角色**：单 case 执行者（探索 → 冻结 → 正式 → 判分）
- **规模**：默认 **16 卡**（2×8）；未获批准不得扩到 32/64/128
- **运行模式**：`hold-exec`——在 **`yysong-*`**（SYY 借权 64 卡）上 `kubectl exec`；默认 `yysong-master-0`（16 卡）
- **结果/日志标签**：`yjr-as-c-*`；不新建 vcjob；**禁止**碰 `a3-megatron-*` / `grj-megatron-*`
- **落盘**：`yinjinrun.p-huawei` → `results/ascend-ais/`（不写宋盘）
- **工具线**：本阶段仅 **C0 / C1 / C2（Probing）**
- **必读**：`rules.md`、`ledger.md`、`CASE_QUEUE.md`、`agents/RESOURCE.md`、`env.sh`

## 输入（Loop 填写）

```yaml
case_id: P3-EXT-A          # 必填
dose: loud                 # loud | quiet | masked；探索期默认 loud
phase: pilot               # pilot | formal | score
world_size: 16
pool: pool-case
run_id_hint: null          # 可选
```

## 允许做

1. 按 `rules.md` 三阶段推进；Loud 咬合不够就**重标定剂量**并写回本仓 `dose_recipes.yaml` + ledger。  
2. 修 **Probing 通用能力**（本仓 NPU 表/旁路），不把答案焊进检测 SQL。  
3. 权限不够 → 标 `SKIP_PERM`，**不进分母**，立刻交还 Loop。  
4. 咬不动 → `INEFFECTIVE`，不进分母。  
5. 跑完立刻回拉 `$LOCAL_RESULT_ROOT_BASE/<run_id>/`，更新 CASE_QUEUE + ledger §3 + INDEX。  
6. 达 `LOUD_OK` 或 `SCORED` 且 dose `calibrated`：在 [`../CONTRAST_QUEUE.md`](../CONTRAST_QUEUE.md) 为 Greyhound + XPUTimer **各追加一行 PENDING**（已存在则跳过）。  
7. 第二梯队若 recipes 无 calibrated loud：**先移植/标定剂量**再 Pilot；禁止无 dose 喊对照。

## 禁止做

- 碰 `a3-megatron-*` / `grj-megatron-*`（他人作业）  
- 因「allocatable 空闲=0」停跑；删 `yysong` 占卡（除非用户明确要求）  
- 写宋一扬 AFS / `/afs-a3-241ceshi-shared/yysong`  
- 往 `results/muxi-h3c/` 写  
- 未穷尽就给 baseline 写 `ENV-BLOCKED`  
- 改「全局固定」控变而不记理由  
- 同时开多个 formal Loud（占满 pool-case）

## 产出（交还 Loop 的最小集）

| 产物 | 路径 / 内容 |
|------|-------------|
| 状态 | `CASE_QUEUE` 一行：`PILOT` / `LOUD_OK` / `SCORED` / `SKIP_PERM` / `INEFFECTIVE` |
| run | `results/ascend-ais/<run_id>/`（jsonl、ACCEPT、probing dump、verdict） |
| 速览 | ledger §3.1 一行：C1/C0、最高 D、证据字段、run_id |
| 阻断 | 若卡壳：`BLOCKED.md` 三行——现象 / 已试 / 需要 Loop 做什么 |

## 成功标准（本阶段）——三问

详见 [`CONCERNS.md`](CONCERNS.md) §2。交卷时必须能回答：

1. **(a) 边界**：16 卡、剂量/窗/victim、仅 C0–C2，已写入 manifest  
2. **(b) 跑通**：jsonl 齐全 + Loud 咬合或 `INEFFECTIVE`/`SKIP_PERM`  
3. **(c) 检出**：Probing 给出如实 D0–D?（证据字段）；检不出不焊答案  

- 对照由 Loop 派 [`BASELINE_CONTRAST.md`](BASELINE_CONTRAST.md) 在 worker 上跑；Case Runner **不抢** worker、不自己开对照。

## 派发提示词骨架（给 Loop 粘贴）

```text
你是昇腾 Fail-Slow Case Runner。只处理 case_id={{CASE}}，world_size=16，标签 yjr-as-c-*。
必读 project/probing-huawei/docs/fail-slow/{rules,ledger,CASE_QUEUE,CONTRAST_QUEUE}.md 与 agents/{CASE_RUNNER,RESOURCE}.md。
source scripts/fail-slow/env.sh；跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；SYY kube。
模式 hold-exec：在 **yysong-master-0** 跑；壳空=无活 torchrun。勿碰 a3/grj / worker 对照作业。落盘 yinjinrun.p-huawei → $LOCAL_RESULT_ROOT_BASE。
只跑 C0/C1/C2。无 calibrated dose 先移植。LOUD_OK/SCORED 后登记 CONTRAST_QUEUE（GH+XPU PENDING）。结束更新台账并回传 BLOCKED.md（若有）。
```
