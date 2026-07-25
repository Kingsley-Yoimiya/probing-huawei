# 华为昇腾 · Baseline 对照队列（流水线 2）

> 流水线 1（Probing Case）产出「注入已生效」后，本表驱动 Greyhound / XPUTimer **同剂量对照**。  
> 任务卡：[`agents/BASELINE_CONTRAST.md`](agents/BASELINE_CONTRAST.md)  
> 剂量真相：`scripts/fail-slow/dose_recipes.yaml`（须 `loud.status=calibrated`）  
> 更新：每完成一对 `(case, tool)` 就改本表 + [`ledger.md`](ledger.md) §3.2。

## 入队 / 终态规则

| 条件 | 动作 |
|------|------|
| Case 达 `LOUD_OK` 或 `SCORED`，且 dose `calibrated` | **入队**：为本波工具各建一行 `PENDING` |
| Case `INEFFECTIVE` / `SKIP_PERM` | **不入队** |
| 对照跑完写 VERDICT+SUMMARY | → `DONE`（`detect_ok` 如实记；无咬合也是 DONE） |
| 环境卡死且已穷尽 | → `BLOCKED` + 短因；Loop 可改派或跳过该 tool |
| 本波不跑的工具（FR/Dynolog） | 不建行 |

**本波工具**：仅 **Greyhound**（`yysong-worker-1`）+ **XPUTimer**（`yysong-worker-2`）。  
**禁止**：改对手判据抬分；焊 Probing 答案；抢 `yysong-master-0`；碰 a3/grj。

状态机：`PENDING` → `RUNNING` → `DONE` | `BLOCKED` | `SKIP`

## 优先序（Loop 挑队头）

1. `P3-EXT-A` × Greyhound（公平性升级后 **重跑**）  
2. 已 SCORED 其余格 × 空闲 tool  
3. 流水线 1 新产出的 `LOUD_OK`/`SCORED` 随时插入  

## 队列（case × tool）

| case_id | tool | 冻结 dose（loud args） | case_ref（Probing） | 状态 | evidence / 备注 |
|---------|------|------------------------|---------------------|------|-----------------|
| P3-EXT-A | Greyhound | `cpu_load=90` | `20260724_231918` | **PENDING** | 旧 S4 `yjr-as-b-gh-s4-20260725_002805` 保留；**须用 collect_seq 真实序列重跑** |
| P3-EXT-A | XPUTimer | `cpu_load=90` | `20260724_231918` | **DONE** | `yjr-as-b-xpu-s4-20260724_233105`；自主 flags=0；跨-run 1.032 无咬合 |
| P1-EXT-A | Greyhound | `inline_cube_size=8192,inline_cube_mm=64` | `20260725_011129` | PENDING | |
| P1-EXT-A | XPUTimer | 同上 | `20260725_011129` | PENDING | |
| P1-EXT-B | Greyhound | `inline_hbm_mb=512,inline_hbm_copies=48` | `20260725_014350` | PENDING | |
| P1-EXT-B | XPUTimer | 同上 | `20260725_014350` | PENDING | |
| P3-EXT-B | Greyhound | `fio_nj=16,...`（见 recipes） | `20260725_020212` | PENDING | |
| P3-EXT-B | XPUTimer | 同上 | `20260725_020212` | PENDING | |
| P3-EXT-C | Greyhound | `vm_n=96,vm_bytes=6G` | `20260725_021906` | PENDING | |
| P3-EXT-C | XPUTimer | 同上 | `20260725_021906` | PENDING | |
| P3-SW-A | Greyhound | `inline_gc_every=1,inline_gc_stall_s=0.25` | `20260725_012957` | PENDING | |
| P3-SW-A | XPUTimer | 同上 | `20260725_012957` | PENDING | |

> 新 case 达 LOUD_OK/SCORED：复制两行（GH+XPU）追加到表尾，状态 `PENDING`。

## 产物路径约定

```text
$LOCAL_RESULT_ROOT_BASE/baseline/<tool>/contrast-<case_id_lower>-<ts>/
  CONTRAST_VERDICT.md
  CONTRAST_SUMMARY.json
  manifest.yaml          # case_id, dose args, window, seed, case_ref, detect_mode
  # + 工具原始 dump（jsonl/prom/…）
```

标签：`yjr-as-b-<gh|xpu>-*`。不覆盖 Probing `results/ascend-ais/<case_run>/`。

## 与流水线 1 的关系

- Case 已 `SCORED`：**跳过**流水线 1，只走本表。  
- Case 正在 C2/D：**允许**对照并行（dose 已冻结），不抢 master。  
- 「跑光 27」= CASE_QUEUE 可跑格终态齐 + SKIP_PERM 写齐 + 本表对所有 calibrated case 的 GH/XPU 均为 `DONE`（或 `BLOCKED` 有因）。
