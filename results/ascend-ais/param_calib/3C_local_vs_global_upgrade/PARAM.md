# PARAM · ③-C 局部升 vs 全局升

> 状态：**DONE** · `3C_local_vs_global_upgrade` · mode=`offline_extrapolate_reuse_3A_local` · 2026-07-27T06:53:28
> harness：`scripts/fail-slow/param_calib/3c_local_vs_global_upgrade.py`
> case=`P3-SW-A` · rate*=`0.001` · W*=100 · victim=7

## 结论：升精度范围 = **`local_suspect_only`**（量比 local/global ≈ **0.0625**）

| 臂 | SET_SCOPE | #ranks↑ | 升详 TT 字节 | MiB | D | 证据 |
|----|-----------|---------|-------------|-----|---|------|
| 局部 suspect | victim | 1 | **2519040** | 2.402 | **D4** | 3A_014151 live SET_SCOPE=victim rate*=0.001 → RSS∧SET∧TT>0 → D4 |
| 全局 all | all | 16 | **40304640** | 38.438 | **D4** | offline_extrapolate: global TT volume = N×local; D≥local by dominance (victim data ⊇); live SET_SCOPE=all INVALID 20260727_012805-3a-p3-sw-a-loud deadlock@L=138 |

## 曲线要点

- **量比** local/global = **0.062500**（≈ **16.0×** 节省）= n_suspects/N = 1/16
- **D-level**：局部 live D4（③-A）；全局由支配论 ≥D4 → **同级**，无归因增益
- **尺**：升详诱导 TT = `#ranks↑ × W* × 25190 B/step`（②-B）；禁止用训练 step_ms / 禁止只报 cold；禁止把 ③-A 全 rank MEMT 满环误当「全 rank 已升」
- **死锁**：live `SET_SCOPE=all` → INVALID `20260727_012805-3a-p3-sw-a-loud` @L=138；本格不重跑
- **Dynolog 对照**：全量 profiler 噪音文献 **+20–44%**；沐曦 P3-SW-A 真跑 ≈**+53%**

### 附：module 维（设计层外推，非主 IV）

| 臂 | module_frac | 升详 TT 字节 |
|----|-------------|-------------|
| local_suspect_module | 0.15 | 377856 |
| global_all_module | 1.0 | 2519040 |

- module 量比（示意）≈ **0.150**（假设嫌疑 module 分数=0.15；本轮无 live module-filter，不作正式 θ）

## 这数据证明为什么这么设

对 P3-SW-A loud：触发后 SET `probing.torch.profiling=on,rate=0.001`，自变量=升精度范围。局部（suspect/victim=7，复用 ③-A `20260727_014151-3a-p3-sw-a-loud`）→ D4；全局（全 16 rank）外推升详 TT 量=40304640 B vs 局部 2519040 B → 量比 local/global=**0.0625**（≈16.0×），D-level **同级 D4**。证明只需对嫌疑维局部升即可同等归因，数据量小一个量级；全局升≈Dynolog 全量噪音对照（文献 +20–44%；沐曦真跑 P3-SW-A≈+53%）。避 live SET_SCOPE=all（INVALID `20260727_012805-3a-p3-sw-a-loud` 多 rank 死锁）。

## 控制变量

| 固定 | 值 |
|------|----|
| case / dose | P3-SW-A / loud |
| rate* | 0.001（③-A） |
| SET 键 | `probing.torch.profiling=` |
| 窗 / victim | [100,300] / 7 |
| W* / B/step | 100 / 25190.4 |
| suspects | {7}（④ 判据 / ④-A） |
| 自变量 | SET_SCOPE ∈ {victim/suspect, all} |

## 证据路径

- ③-A 局部臂：`param_calib/3A_upgrade_rate/20260727_014151-3a-p3-sw-a-loud/`
- ④ 判据 / suspects：`param_calib/4_health_summary_criteria/`
- ④-A 量比对齐：`param_calib/4A_federated_denoise/`（fed/naive≈0.0626）
- INVALID 全局 SET：`param_calib/3A_upgrade_rate/20260727_012805-3a-p3-sw-a-loud/`
- 本格：`param_calib/3C_local_vs_global_upgrade/{PARAM.json,PARAM.md}`

