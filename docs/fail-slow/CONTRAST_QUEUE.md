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
| P3-EXT-A | Greyhound | `cpu_load=90` | `20260724_231918` | **DONE** | `contrast-p3-ext-a-20260725_114502`；collect_seq period=8；coll=1.048；Rbeast cp C0/C1=0；step_ms=1.922 dose_OK；detect_ok=no；旧 S4 保留 |
| P3-EXT-A | XPUTimer | `cpu_load=90` | `20260724_231918` | **DONE** | `yjr-as-b-xpu-s4-20260724_233105`；自主 flags=0；跨-run 1.032 无咬合 |
| P1-EXT-A | Greyhound | `inline_cube_size=8192,inline_cube_mm=64` | `20260725_011129` | **DONE** | `contrast-p1-ext-a-20260725_120526`；coll=1.018 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=3.924 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-EXT-A | XPUTimer | 同上 | `20260725_011129` | **DONE** | `contrast-p1-ext-a-20260725_114546`；自主 flags=0；跨-run coll=1.036 无咬合；dose_check step_ms=3.955 PASS |
| P1-EXT-B | Greyhound | `inline_hbm_mb=512,inline_hbm_copies=48` | `20260725_014350` | **DONE** | `contrast-p1-ext-b-20260725_121407`；coll=1.009 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=2.070 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-EXT-B | XPUTimer | 同上 | `20260725_014350` | **DONE** | `contrast-p1-ext-b-20260725_115717`；自主 hang/slow=0；跨-run coll=0.982 无咬合；dose_check step_ms=2.069 PASS；detect_mode=cross_run_contrast |
| P3-EXT-B | Greyhound | `fio_nj=16,...`（见 recipes） | `20260725_020212` | **DONE** | `contrast-p3-ext-b-20260725_122204`；coll=1.049 FAIL；Rbeast C1/C0 cp=0/0 → miss；step_ms=1.738 dose_OK；detect_ok=no；detect_mode=no_bite；无 fio→stress-ng hdd+iomix |
| P3-EXT-B | XPUTimer | 同上 | `20260725_020212` | **DONE** | `contrast-p3-ext-b-20260725_120235`；自主 hang/slow=0；跨-run coll=1.048 无咬合；dose_check step_ms=1.793 PASS；detect_mode=cross_run_contrast；detect_ok=no；无 fio→stress-ng hdd+iomix |
| P3-EXT-C | Greyhound | `vm_n=96,vm_bytes=6G` | `20260725_021906` | **DONE** | `contrast-p3-ext-c-20260725_124257`；coll=1.296 FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=1.063 dose_WEAK；detect_ok=no；detect_mode=no_bite；warmup 预热 page-in（+6Gi）；先轮 `123310` coll=1.144 |
| P3-EXT-C | XPUTimer | 同上 | `20260725_021906` | **DONE** | `contrast-p3-ext-c-20260725_121535`；自主 hang/slow=0；跨-run coll=1.184 无咬合；dose_check step_ms=1.780 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-A | Greyhound | `inline_gc_every=1,inline_gc_stall_s=0.25` | `20260725_012957` | **DONE** | `contrast-p3-sw-a-20260725_124837`；coll=1.000 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=5.667 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P3-SW-A | XPUTimer | 同上 | `20260725_012957` | **DONE** | `contrast-p3-sw-a-20260725_122733`；自主 hang/slow=0；跨-run coll=0.953 无咬合；dose_check step_ms=2.633 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-A | Greyhound | `chunks=12,stall_mb=768,stall_s=0.25` | `20260725_114556` | **DONE** | `contrast-p1-sw-a-20260725_125949`；coll=1.009 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=4.283 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-SW-A | XPUTimer | 同上 | `20260725_114556` | **DONE** | `contrast-p1-sw-a-20260725_123626`；自主 hang/slow=0；跨-run coll=0.991 无咬合；dose_check step_ms=4.284 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-B | Greyhound | `rare_seq=1536,every=1` | `20260725_115732` | **DONE** | `contrast-p1-sw-b-20260725_132011`；coll=1.020 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=1.386 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-SW-B | XPUTimer | 同上 | `20260725_115732` | **DONE** | `contrast-p1-sw-b-20260725_124414`；自主 hang/slow=0；跨-run coll=0.991 无咬合；dose_check step_ms=1.372 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-C | Greyhound | `n=1024,every=1,fallback_s=0.25` | `20260725_121105` | **DONE** | `contrast-p1-sw-c-20260725_132954`；coll=1.027 FAIL；Rbeast C1/C0 cp=0/0 miss；median step_ms=1.024 盲；tip max=4.038 PASS（金标 tip max=4.63）；detect_ok=no；detect_mode=no_bite |
| P1-SW-C | XPUTimer | 同上 | `20260725_121105` | **DONE** | `contrast-p1-sw-c-20260725_125656`；自主 hang/slow=0；跨-run coll=0.991 无咬合；median step_ms=1.006 盲；tip max=4.897 PASS（金标 tip max=4.63）；detect_mode=cross_run_contrast；detect_ok=no |
| P2-SW-B | Greyhound | `algo=ring,stress_mb=512,buffsize=8` | `20260725_122911` | **DONE** | `contrast-p2-sw-b-20260725_134521`；coll=0.982 FAIL；Rbeast C1/C0 cp=0/0 miss；dose_check **comm=1.862 PASS**（step=1.152 旁证）；detect_ok=no；detect_mode=no_bite |
| P2-SW-B | XPUTimer | 同上 | `20260725_122911` | **DONE** | `contrast-p2-sw-b-20260725_131251`（代理 timeout 后收）；自主 hang/slow=0；跨-run coll=1.000 无咬合；dose_check **comm=1.875 PASS**（step=1.152 旁证）；detect_mode=cross_run_contrast；detect_ok=no；先轮 `130800` coll=0.991/comm=1.850 |
| P2-SW-C | Greyhound | `device_rev=1,topo_extra_ar=512,topo_ar_elems=262144` | `20260725_124102` | **DONE** | `contrast-p2-sw-c-20260725_135623`；coll=0.564 FAIL；Rbeast C1/C0 cp=0/0 miss；dose_check **comm=15.016 PASS**（step=2.211 旁证；金标≈49.86）；detect_ok=no；detect_mode=no_bite |
| P2-SW-C | XPUTimer | 同上 | `20260725_124102` | **DONE** | `contrast-p2-sw-c-20260725_132235`；自主 hang/slow=0；跨-run coll=0.593 无咬合；dose **comm=13.910 PASS**（step=2.119）；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-B | Greyhound | `mb=16,stall_s=0.25` | `20260725_125558` | **DONE** | `contrast-p3-sw-b-20260725_140639`；coll=0.984 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=3.760 dose_OK（金标≈2.06）；detect_ok=yes；detect_mode=autonomous |
| P3-SW-B | XPUTimer | 同上 | `20260725_125558` | **DONE** | `contrast-p3-sw-b-20260725_133435`；自主 hang/slow=0；跨-run coll=0.992 无咬合；dose_check step_ms=2.047 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-C | Greyhound | `cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0,max_chunks=64` | `20260725_135238` | **DONE** | `contrast-p3-sw-c-20260725_143448`；coll=1.016 FAIL；Rbeast C1 cp=1 / C0=0 → hit；step_ms=2.509 dose_OK（金标≈2.49）；detect_ok=yes；detect_mode=autonomous；sidecar 8c stress=yes nproc=320；旁证 `143010` step=2.361 同结论；`142010` 无注入作废 |
| P3-SW-C | XPUTimer | 同上 | `20260725_135238` | **DONE** | `contrast-p3-sw-c-20260725_141815`；自主 hang/slow=0；跨-run coll=0.917 无咬合；dose_check step_ms=2.504 PASS（金标≈2.49）；detect_mode=cross_run_contrast；detect_ok=no |
| P1-HW-B | Greyhound | `inline_hbm_mb=512,inline_hbm_copies=6,inline_hbm_copies_max=48,ramp=1` | `20260725_142359` | **DONE** | `contrast-p1-hw-b-20260725_144607`；coll=1.027 FAIL；Rbeast C1 cp=2 / C0=0 → hit；step_ms=1.609 dose_OK（金标≈1.57）；detect_ok=yes；detect_mode=autonomous；GH 队无 PENDING |
| P1-HW-B | XPUTimer | 同上 | `20260725_142359` | **DONE** | `contrast-p1-hw-b-20260725_143531`；自主 hang/slow=0；跨-run coll=0.991 无咬合；dose_check step_ms=1.585 PASS（金标≈1.57）；detect_mode=cross_run_contrast；detect_ok=no |

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
