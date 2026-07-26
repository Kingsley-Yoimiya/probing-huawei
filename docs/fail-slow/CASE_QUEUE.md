# 华为昇腾 · 27-case 排期与权限跳过

> 故障定义：`project/reading-paper/writing/probing-paper/OUTLINE-v3-27-cases-per-cell.md`  
> 状态机：`PENDING` → `PILOT` → `LOUD_OK` → `SCORED` / `SKIP_PERM` / `INEFFECTIVE`  
> 默认规模：**16 卡**（`yjr-as-c-*`）；派发任务卡见 [`agents/CASE_RUNNER.md`](agents/CASE_RUNNER.md)  
> **对照流水线**：已 `LOUD_OK`/`SCORED` 且 dose calibrated → [`CONTRAST_QUEUE.md`](CONTRAST_QUEUE.md)  
> 更新：跑完一格就改本表 + [`ledger.md`](ledger.md) §3。

## 总览

| 格 | A | B | C |
|---|---|---|---|
| **P1×HW** | P1-HW-A 热墙渐进降频 | P1-HW-B 显存带宽渐进衰减 | P1-HW-C 功耗墙间歇 |
| **P1×SW** | P1-SW-A 显存碎片化 | P1-SW-B 动态 shape 次优 kernel | P1-SW-C 首次编译尖刺 |
| **P1×EXT** | P1-EXT-A 同卡算力抢占 | P1-EXT-B 同卡带宽争用 | P1-EXT-C 共享时间片抖动 |
| **P2×HW** | P2-HW-A 光模块误码 | P2-HW-B 机内链路漂移 | P2-HW-C 交换机拥塞 |
| **P2×SW** | P2-SW-A 健康检查回退 | P2-SW-B 通信算法切换 | P2-SW-C 拓扑映射漂移 |
| **P2×EXT** | P2-EXT-A 邻居持续网压 | P2-EXT-B 邻居突发上传 | P2-EXT-C 共享存储带宽 |
| **P3×HW** | P3-HW-A 内存 ECC/换页 | P3-HW-B 主机 CPU 温度墙 | P3-HW-C 本地盘延迟 |
| **P3×SW** | P3-SW-A 对象泄漏→GC | P3-SW-B dataloader 泄漏 | P3-SW-C 监控自身泄漏 |
| **P3×EXT** | P3-EXT-A 抢 CPU | P3-EXT-B 抢磁盘 IO | P3-EXT-C 抢内存带宽 |

## 排期

### 第一梯队（已完成 · 跳过流水线 1 → 进对照）

| Case | 注入思路（昇腾） | 权限 | 状态 | 备注 |
|------|------------------|------|------|------|
| P1-EXT-A | 同卡 INLINE cube | ✅ | **SCORED** | C1/C0=**3.87**@`011129`；**D2**；对照见 CONTRAST_QUEUE |
| P1-EXT-B | inline HBM | ✅ | **SCORED** | C1/C0=**2.02**@`014350`；**D3** |
| P3-EXT-A | `stress-ng` CPU | ✅ | **SCORED** | C1/C0≈1.97；**D3**；GH 公平性重跑对照优先 |
| P3-EXT-B | `fio` IO | ✅ | **SCORED** | C1/C0=**2.13**@`020212`；**D3** |
| P3-EXT-C | `stress-ng --vm` | ✅ | **SCORED** | C1/C0=**1.59**@`021906`；**D3** |
| P3-SW-A | inline `8a` GC/stall | ✅ | **SCORED** | C1/C0=**2.93**@`012957`；**D4** |
| P1-SW-A | inline `2a` 显存碎片 | ✅ | **SCORED** | C1/C0=**4.20**@`114556`；**D3**；对照见 CONTRAST_QUEUE |
| P1-SW-B | inline `2b` 罕见 shape | ✅ | **SCORED** | C1/C0=**1.36**@`115732`；**D3**；对照见 CONTRAST_QUEUE |
| P1-SW-C | inline `2c` 编译尖刺 | ✅ | **SCORED** | tip max=**4.63**@`121105`；**D3**；median盲；对照见 CONTRAST_QUEUE |

### 第二梯队（流水线 1 优先序 · 逐格）

> 缺 `dose_recipes` calibrated 时：Case Runner **先移植剂量**再 Loud。

| Case | 权限 | 状态 | 注入思路（草稿） | 备注 |
|------|------|------|------------------|------|
| P2-SW-B | ✅ | **SCORED** | HCCL 算法+buff 钳制 | C1/C0_comm=**1.82**@`122911`；**D3**；对照见 CONTRAST_QUEUE |
| P2-SW-C | ✅ | **SCORED** | 拓扑映射漂移 | C1/C0_comm=**49.86**/step=**5.06**@`124102`；**D3**；对照见 CONTRAST_QUEUE |
| P3-SW-B | ✅ | **SCORED** | dataloader 泄漏 | C1/C0=**2.06**@`125558`；**D4**；dose `mb=16,stall_s=0.25` calibrated；对照见 CONTRAST_QUEUE |
| P3-SW-C | ✅ | **SCORED** | 监控自身泄漏 | C1/C0=**2.33**@`135238`（pod-sup 准时 inject@step100）；**D4**；dose `cpu_n=nproc,cpu_load=90,mb=1` calibrated；对照见 CONTRAST_QUEUE |
| P1-HW-B | ✅ | **SCORED** | INLINE 渐进 HBM 6→48 | C1/C0=**1.57**@`142359`；**D3**；dose calibrated；对照见 CONTRAST_QUEUE |

### 第三梯队（已批量 SKIP_PERM · 2026-07-25 Loop 开跑）

| Case | 状态 | 原因 |
|------|------|------|
| P1-HW-A / P1-HW-C | **SKIP_PERM** | 改频 / power cap |
| P1-EXT-C | **SKIP_PERM** | 需卡共享调度配置 |
| P2-HW-A/B/C | **SKIP_PERM** | 网络组 / 交换机 / 固件 |
| P2-SW-A | **SKIP_PERM** | 需通信库插件权限时跳过 |
| P2-EXT-A/B/C | **SKIP_PERM** | 第二 job / 隔离网 / 存储侧 |
| P3-HW-A/B/C | **SKIP_PERM** | ECC 真值 / 改频 / 盘级配合 |

> **SKIP_PERM 不进覆盖率分母、不进 CONTRAST_QUEUE**。权限放开再改状态重排。

## Loop 执行顺序（双流水线）

1. **流水线 1**：跳过已 SCORED → 批量 SKIP_PERM（第三梯队）→ 按第二梯队表序 Pilot/Score。  
2. **流水线 2**：已 LOUD_OK/SCORED + calibrated → [`CONTRAST_QUEUE.md`](CONTRAST_QUEUE.md)（GH@w1 / XPU@w2 并行）。  
3. Case 新达 LOUD_OK/SCORED：立刻追加对照两行；C2 可与对照并行（不抢 master 给对照）。

每完成一步：更新本表 + CONTRAST_QUEUE（若适用）+ `ledger.md` §3 + `$LOCAL_RESULT_ROOT_BASE/INDEX.md`。
