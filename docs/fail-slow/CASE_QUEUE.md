# 华为昇腾 · 27-case 排期与权限跳过

> 故障定义：`project/reading-paper/writing/probing-paper/OUTLINE-v3-27-cases-per-cell.md`  
> 状态机：`PENDING` → `PILOT` → `LOUD_OK` → `SCORED` / `SKIP_PERM` / `INEFFECTIVE`  
> 默认规模：**16 卡**（`yjr-as-c-*`）；派发任务卡见 [`agents/CASE_RUNNER.md`](agents/CASE_RUNNER.md)  
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

## 排期（仿沐曦：先零审批）

### 第一梯队（优先 Loud pilot）

| Case | 注入思路（昇腾） | 权限 | 状态 | 备注 |
|------|------------------|------|------|------|
| P1-EXT-A | 同卡 INLINE cube（外挂隔离无效） | ✅ | **SCORED** | Loud PASS C1/C0=**3.87**@`011129`（8192×64）；**D2**；D3 定位错不升；末次加剂有效 |
| P1-EXT-B | inline HBM（外挂隔离无效） | ✅ | **SCORED** | Loud PASS C1/C0=**2.02**@`014350`（512×48）；**D3**；SQL attach 失败不升 D4；dose calibrated |
| P3-EXT-A | `stress-ng` CPU，host_bound | ✅ | SCORED | C1/C0≈1.97；C2 @`20260725_001251-yjr-as-c-p3-ext-a-loud` **D3**（SQL_PENDING→D4 未升）；wheel 0.2.6 |
| P3-EXT-B | `fio` IO（同盘 ckpt+payload） | ✅ | **SCORED** | Loud PASS C1/C0=**2.13**@`020212`（fio nj16）；**D3**；SQL attach/PSI 未升 D4；dose calibrated |
| P3-EXT-C | `stress-ng --vm` | ✅ | **SCORED** | Loud PASS C1/C0=**1.59**@`021906`（96×6G）；**D3**；PSI_UNAVAIL；dose calibrated |
| P3-SW-A | inline `8a` GC/stall | ✅ | **SCORED** | Loud PASS C1/C0=**2.93**@`012957`（stall=0.25）；**D4**（RSS SQL）；dose calibrated |

### 第二梯队

| Case | 权限 | 状态 | 备注 |
|------|------|------|------|
| P1-HW-B | ✅ | PENDING | 带宽 sidecar；需 NPU 访存 kernel |
| P1-SW-A / B / C | ✅ | PENDING | 纯软件；脚本移植后开 |
| P2-SW-B / C | ✅ | PENDING | HCCL env / message size |
| P3-SW-B / C | ✅ | PENDING | 仿沐曦 inline / sidecar |

### 第三梯队（权限或审批 → 先 SKIP）

| Case | 默认处置 | 原因 |
|------|----------|------|
| P1-HW-A / P1-HW-C | SKIP_PERM | 改频 / power cap |
| P1-EXT-C | SKIP_PERM | 需卡共享调度配置 |
| P2-HW-A/B/C | SKIP_PERM | 网络组 / 交换机 / 固件 |
| P2-SW-A | SKIP_PERM 或 PILOT | 需通信库插件权限时跳过 |
| P2-EXT-A/B/C | SKIP_PERM | 第二 job / 隔离网 / 存储侧 |
| P3-HW-A/B/C | SKIP_PERM | ECC 真值 / 改频 / 盘级配合 |

> 原则：**SKIP_PERM 不进覆盖率分母**；权限突然放开再改状态重排。

## 建议执行顺序（开跑后）

1. Smoke：16 卡或先 1×8 写 jsonl（无注入）  
2. Loop 派 Case：P3-EXT-A → P1-EXT-A → P3-SW-A → …（第一梯队）  
3. **并行**（另池）：Greyhound / XPUTimer 适配 Agent（见 `agents/`）；**不挡**本表遍历  
4. 某 baseline 达 S4 后，Loop 再开对照波次（同剂量换工具），不回改已打的 Probing 分  

每完成一步：更新本表状态 + `ledger.md` §3 + `results/ascend-ais/INDEX.md`。
