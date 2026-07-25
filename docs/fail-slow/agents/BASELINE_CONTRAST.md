# 任务卡 · Baseline Contrast（流水线 2）

> Loop 派发 **一个 (tool, case_id)** 对照时使用。  
> **不是**再做 S0–S2 适配；GH/XPU 已 S4。本卡只做：冻结 dose → 同条件挂工具 → 自主/跨-run 判定 → 存档。

## 身份

| 项 | Greyhound | XPUTimer |
|----|-----------|----------|
| Pod | `yysong-worker-1` | `yysong-worker-2` |
| 标签 | `yjr-as-b-gh-*` | `yjr-as-b-xpu-*` |
| 适配代码 | `probing-test/.../platform/ascend/greyhound/` | `.../xputimer/` |
| STATUS（适配态） | `baseline/greyhound/STATUS.md` | `baseline/xputimer/STATUS.md` |
| 队列 | [`../CONTRAST_QUEUE.md`](../CONTRAST_QUEUE.md) | 同左 |

- kubectl：`/root/.cache/volcano/kubectl/kubectl` + SYY kube（见 [`IDENTITY.md`](../IDENTITY.md)）  
- 落盘：`yinjinrun.p-huawei` → `$LOCAL_RESULT_ROOT_BASE/baseline/<tool>/contrast-...`  
- **禁止**：抢 `yysong-master-0`；碰 a3/grj；写宋 AFS；改对手阈值抬分；把 Probing SQL/注入窗焊进检测器

## 输入（Loop 填写）

```yaml
tool: greyhound            # greyhound | xputimer
case_id: P1-EXT-A
dose: loud                 # 必须已 calibrated
case_ref: 20260725_011129-yjr-as-c-p1-ext-a-loud   # Probing 金标准 run
world_size: 16
pool: pool-gh              # 或 pool-xpu
```

剂量从 `scripts/fail-slow/dose_recipes.yaml` 的 `cases.<id>.loud` 读取；窗默认 `[100,300]`（与 recipes `inject_measure_window` 一致，除非 case 另有冻结）。

## 步骤

1. 将 `CONTRAST_QUEUE` 该行标 `RUNNING`。  
2. 确认目标 worker **IDLE**（无活 torchrun）；清自己残留，勿杀他人。  
3. **复用/仿写**发射脚本：  
   - 参考 `s4_p3exta_contrast.sh`（同目录）；新 case 可写 `contrast_<case>.sh` 落 `platform/ascend/<tool>/`。  
   - C0：无注入 + 工具挂载；C1：同 dose 注入 + 工具挂载；world=16。  
4. 判定：  
   - **Greyhound**：coll C1/C0；Rbeast 用 `collect_seq` 真实 per-rank 序列 + **C0 假阳性对照**；step_ms 仅作 dose_check（非 GH 规则）。  
   - **XPUTimer**：分列 **自主** hang/slow flags vs **跨-run** 中位比；勿把 cross-run 误标 autonomous。  
5. 写 `CONTRAST_VERDICT.md` + `CONTRAST_SUMMARY.json` + `manifest.yaml`；回拉本机。  
6. 更新 `CONTRAST_QUEUE` → `DONE`（或 `BLOCKED`）；ledger §3.2 一行；**不改** CASE_QUEUE 的 Probing 分。

## 产出

| 产物 | 要求 |
|------|------|
| CONTRAST_VERDICT.md | detect_mode、自主 vs oracle、比率、是否咬合 |
| CONTRAST_SUMMARY.json | 机器可读；含 `case_id`/`tool`/`dose`/`detect_ok` |
| manifest.yaml | case_ref、窗、seed、脚本路径 |
| 队列行 | DONE / BLOCKED |

## 成功标准

- 剂量与 Probing 金标准一致（dose_check 可对 step_ms 或等价墙钟）。  
- 工具规则路径跑通；无咬合也如实记（能力边界）。  
- 公平性规则已遵守（见 greyhound/xputimer NOTES）。

## 派发提示词骨架

```text
你是昇腾 Baseline Contrast Agent。tool={{TOOL}} case_id={{CASE}}。
必读：docs/fail-slow/{CONTRAST_QUEUE,rules,ledger}.md 与 agents/{BASELINE_CONTRAST,BASELINE_COMMON,RESOURCE}.md。
source scripts/fail-slow/env.sh；跳板 kubectl=/root/.cache/volcano/kubectl/kubectl。
只在 {{POD}} 上跑 16 卡对照；冻结 dose 来自 dose_recipes.yaml；case_ref={{REF}}。
禁止抢 master-0 / a3/grj / 宋 AFS；禁止改对手阈值；不覆盖 Probing 分数。
结束：写 contrast-* 产物，更新 CONTRAST_QUEUE + ledger §3.2。
```
