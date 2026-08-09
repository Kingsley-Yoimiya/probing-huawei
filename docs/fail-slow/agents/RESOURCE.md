# 资源划分（自升 16 卡优先 · GRJ 空闲借用）

> 具体 node / pod 填进 [`../ledger.md`](../ledger.md) §1。  
> 与沐曦隔离、三问见 [`CONCERNS.md`](CONCERNS.md)。  
> myportal 总则：`.cursor/rules/cluster-identity.mdc` / `config/CURRENT.md`。

## 硬原则（2026-08-10 对齐）

1. **集群仍可用且可升卡**（借 `songyiyang.p` kube 进 `vc-a3-241ceshi`）。  
   按任务规模提交自有 `yjr-*` vcjob：小任务 **16 卡**，大面 64 / 512…
2. **小任务默认 = 自升一台 16 卡**（编译 / smoke / 短测 / Agent 侧生产验）。  
   **不要**默认去蹭他人 hold 壳。跑完自有 `yjr-*` **只停不删**（`vcctl job suspend`）。
3. **`yysong`（原 4×16=64 hold）已不可用**。  
   历史「主池 yysong」一律作废。若对象偶发仍在：**禁止**删/改 vcjob，也**不要**默认当可用池；仍禁止写宋盘 / 宋 AFS。
4. **现存 hold 借用 = `grj-megatron-32card-0716`**（2 pod × ~16）。  
   **不一定空闲**。仅 `pgrep` 确认无对方训练、或用户明示时才 `kubectl exec`。  
   落盘仍 `yinjinrun.p-huawei`。对方进程再现 → **立刻停我们的作业让路**。不删 vcjob、不写对方盘。
5. **空 = 目标 pod 内没有活训练**（无活 `torchrun` / megatron）。僵尸可忽略。
6. **仍禁止**：`a3-megatron-*`（张文胜）；写宋 AFS / `geruijun` / `grj-shared-log-ckpt`。
7. **落盘**：结果 → `results/ascend-ais/` + AFS `yinjinrun.p-huawei`。  
   - 自升 `yjr-*` / 一般 pod：优先 weight-share；若有 `/data/yinjinrun.p-huawei` 可用则可选  
   - grj：**无** `/data/yinjinrun.p-huawei` → 用  
     `POD_BUNDLE`（可读 `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`）+  
     `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`
8. **跳板 kubectl**（PATH 无 kubectl）：

```bash
K=/root/.cache/volcano/kubectl/kubectl
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
```

## 占卡壳登记

### 自升 · 16 卡（默认 · Agent 小任务）

| 方式 | 说明 |
|------|------|
| 自有 vcjob `yjr-*-16`（或等价 1×16） | 编译 / smoke / 短测优先；跑完 suspend，不 delete |
| 更大面 `yjr-*` 64 / 512… | 需要规模时再升；不要为小任务占大面 |

### grj-megatron-32card-0716 · 32（空闲借用 · 非默认）

| pod | 卡 | 角色（仅 IDLE 时） |
|-----|----|-------------------|
| `grj-megatron-32card-0716-master-0` | ~16 | Dose / Case / 编译备选 |
| `grj-megatron-32card-0716-worker-0` | ~16 | Pillar C 备选 |

### yysong · 64（历史 · 已不可用）

| pod | 说明 |
|-----|------|
| `yysong-master-0` / `worker-0..2` | 原主池 4×16；**2026-08-10 起勿默认使用** |

## 默认同时刻配额（Dose + C · 2026-08-10）

| 角色 | 卡 | 落点（优先） | 备选 |
|------|-----|--------------|------|
| Dose / Case ≤1 | 16 | **自升 16 卡 `yjr-*`** | grj-m0（IDLE + 让路） |
| Pillar C ≤1 | 16 | **自升 16 卡 `yjr-*`** | grj-w0（IDLE + 让路） |
| Greyhound / XPU ≤1 | ≤16 | 自升 16 或错峰 | grj（IDLE） |
| Loop 父 | 0 | — | — |

标签 `yjr-as-c-*` / `yjr-as-b-*` 只用于 **run_id / 结果目录**；真正占卡用自有 vcjob 名或确认 IDLE 的 grj hold-exec。

## Probing wheel / Rust（铁律）

**不要在 hold pod 里 rustup / 删 toolchain 重装 / 裸拉 crates.io**——集群 egress 极慢（P-FIX 已踩坑）。  
真相源：[**`BUILD_WHEEL.md`**](BUILD_WHEEL.md)（复用 → Mac 摆渡 → 可选反代）。  
编译位置：优先自升 16 卡；GRJ 仅 IDLE。

## 检查清单

- [ ] SYY kube + `JUMP_KUBECTL`  
- [ ] 小任务：已自升 / 将自升 16 卡（非默认蹭 hold）  
- [ ] 若走 grj：目标 pod `pgrep` IDLE；结果写 weight-share；对方再现让路  
- [ ] 不把 `yysong` 当可用主池  
- [ ] 结果写 `ascend-ais` / `yinjinrun.p-huawei`  
- [ ] 不改坏沐曦 dose / 共享脚本默认  
- [ ] **仍不碰 a3-megatron-***  
- [ ] 编/装 probing：**先读** [`BUILD_WHEEL.md`](BUILD_WHEEL.md)；禁 pod 内 rustup 重装  

## 编 wheel（摘要）

详见 [`BUILD_WHEEL.md`](BUILD_WHEEL.md)。集群 egress 下大文件极慢；**本机 `:7897` → scp → 跳板 → kubectl cp**。已有 `wheels/*.whl` 优先只重装；**禁止** `rm -rf` toolchain 再 `rustup install`。

## 登记模板

```text
mode: self-vcjob-preferred   # 小任务自升 16；大面再升卡
hold_job_borrow:  grj-megatron-32card-0716   # IDLE only; yield if owner returns
hold_job_legacy:  yysong                     # unavailable; do not default
kubectl_on_jump: /root/.cache/volcano/kubectl/kubectl
pool-default: self yjr-*-16  world=16
pool-borrow:  grj-m0 / grj-w0 when IDLE
afs_env:
  POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
  POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
never:      a3-megatron-*, 写宋 AFS /afs-a3-241ceshi-shared/yysong, geruijun/*, grj-shared-log-ckpt, 删对方 vcjob
```
