# 开跑前再确认的三件事

> 回答「资源会不会打架」「Case 到底盯什么」「Baseline 和沐曦什么关系」。

---

## 0. 编 Probing wheel 别在集群下工具链

**会反复踩**：pod 内 `rustup toolchain install` / `curl static.rust-lang.org` 极慢或半残（`Missing manifest`）。  
**正确**：本机 Clash → 文件摆渡（whl 或完整 toolchain）；禁止删 `RUSTUP_HOME` 再装。  
细则：[**`BUILD_WHEEL.md`**](BUILD_WHEEL.md)。`install_probing_wheel_on_pod.sh` 缺 rustc → 直接 FAIL，不得静默公网安装。（2026-07-26 P-FIX 又踩一次。）

---

## 1. 运行资源与冲突

### 同时大概多少卡、几个 Agent？

| 角色 | 数量 | 占卡（默认） | 说明 |
|------|------|--------------|------|
| Loop 父 | 1 | 0 | 只编排，自己不起训 |
| Case Runner | **1 inflight** | **16** | 同一时刻只跑一个 formal case |
| Greyhound 适配 | 0～1 | **另 16** | 有空闲池才开 |
| XPUTimer 适配 | 0～1 | **另 16** | 有空闲池才开 |
| FR（可选） | 0～1 | 8～16 | 默认关 |

**满配峰值**：约 **48 卡**（16×3）+ 可选 FR；卡不够时砍 Baseline 池，**保 Case 16 卡**。  

**空闲含义**：我们管的 64 卡 = **`yysong`**（SYY 借权发射的占卡作业）。调度器常显示空闲=0。  
**`yysong` 内无活训练 = 可以跑**。  
**`grj-megatron-32card-0716`**：2026-07-25 起允许**空闲借用**（无对方训练才可跑；落盘仍 `yinjinrun.p-huawei`；对方再现立刻让路）。  
**`a3-megatron` 仍禁止碰**。跳板 kubectl：`/root/.cache/volcano/kubectl/kubectl`。

### 会不会和沐曦脚本/文件冲突？

**正常按边界跑，不会。** 隔离层：

| 层 | 昇腾 | 沐曦 |
|----|------|------|
| 集群 | `vc-a3-241ceshi` + SYY | `vc-c550-*` + weibozhen 等 |
| 作业前缀 | `yjr-as-*` | `yjr-fs64-*` / `yjr-swb-*` 等 |
| 本机结果 | `results/ascend-ais/` | `results/muxi-h3c/` |
| AFS | `yinjinrun.p-huawei` | `yinjinrun.p` |
| 规则台账 | `probing-huawei/docs/fail-slow/` | `probing-test/docs/fail-slow/` |
| Probing 包 | `probing-huawei`（NPU） | MetaX 构建 |
| 剂量 | 本仓 `dose_recipes.yaml` | 沐曦那份（勿互相覆盖） |

**共享但只读/按平台分支用的**：`probing-test/scripts/fail-slow/` 编排。约定：

- 昇腾发射前 `source probing-huawei/scripts/fail-slow/env.sh`（强制 SYY kube、`LOCAL_RESULT_ROOT_BASE=ascend-ais`）。  
- **禁止**改共享脚本里的沐曦默认去「顺便修昇腾」；平台差分进 `platform/ascend/` 或本仓薄包装。  
- 多 Agent 大改仍可放 `results/ascend-ais/.../campaign/`，避免抢主路径。

---

## 2. Case 核心关注点（最重要）

每个 case 子 Agent / Loop 验收时，**只盯三问**（顺序固定）：

### (a) 条件与边界怎么设？

| 项 | 默认边界 |
|----|----------|
| 规模 | **16 卡**；未经批准不上 32+ |
| 控变 | ledger §2.1（模型/seed/iters/窗）；改全局固定先停 |
| 剂量 | 本仓 `dose_recipes.yaml`；Loud 先咬合，Quiet/Masked 后置 |
| 注入窗 | 默认 [100,300]（warmup 50 → 全局约 150–350）；**检测 SQL 不得写死此窗** |
| victim | 默认 local_rank=7（节点卡数不足则改并记 ledger） |
| 工具线 | 仅 C0 / C1 / C2（Probing）；本阶段无 baseline |
| 权限 | `SKIP_PERM` → 不进分母，换下一格 |
| 平行 run | C0 健康 / C1 纯注入 / C2 注入+Probing；比的是同 seed、对齐窗 |

故障语义仍以论文 OUTLINE 为准；昇腾只改**注入实现与可观测字段**，不改「这一格在测什么」。

### (b) 能不能跑得通？

最低门闩（过不了别谈检测）：

1. 训练写出齐全 `rank_*.jsonl`（16 rank）  
2. C0 中位 step 稳定（CV 别炸）  
3. C1 Loud：`accept_loud` 过阈 → 否则抬剂量；仍不动 → `INEFFECTIVE`  
4. 产物回拉 `results/ascend-ais/<run_id>/`

### (c) Probing 有没有实力检出？

在 (b) 成立后才判：

| 级 | 含义（摘要） |
|----|----------------|
| D0 | 跑了检测但无异常 |
| D1+ | 有异常信号 |
| D2+ | 时间窗对得上（判分阶段才看 GT） |
| D3+ | 对象对（P1/P2→rank；P3→host） |
| D4+ | 根因层坐标对（可走 SQL 或同窗 `npu-smi`/PSI 旁路，**非** pgrep 答案） |

如实打分；检不出就 D0，去修**通用**采集/查询，**禁止**把答案焊进 SQL。  
本阶段**不要求**和 Greyhound 等比——那是对照波次。

---

## 3. Baseline：和沐曦同一套思想

是的——**适配战，不是重写论文指标**。

| 点 | 约定 |
|----|------|
| 目标 | 采集非空 + 用**它自己的规则/代码**做检测（标清自主 vs oracle） |
| 红线 5 | 未穷尽 → `PENDING`，不早下 `ENV-BLOCKED` |
| 形式 | stub → 真 hook → 对照；允许与 CUDA/MetaX 实现不同 |
| 资源 | 另池 `yjr-as-b-*`，**不挡** 27-case |
| 细节 | `BASELINE_COMMON.md` + 各工具任务卡；技术参考 `platform/ascend/BASELINE_PORTING.md` |

Case 出分母；Baseline 出「对手在昇腾上能不能干活」；两边就绪再并对照。
