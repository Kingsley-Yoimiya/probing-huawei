# 资源划分（yysong 64 主池 + grj 空闲借用 32）

> 具体 node / pod 填进 [`../ledger.md`](../ledger.md) §1。  
> 与沐曦隔离、三问见 [`CONCERNS.md`](CONCERNS.md)。

## 硬原则（2026-07-27 对齐）

1. **主池 64 卡 = `yysong` vcjob**（submitter=`songyiyang.p`，4 pod × 16）。  
   借 SYY kube 进壳；**直接 `kubectl exec`**。不要新建抢调度的 vcjob。  
   **「勿碰 yysong」≠ 禁止 hold-exec**：允许进 `yysong-*` 跑我们的实验；禁止的是写宋盘、删/改 `yysong` vcjob、动宋一扬家目录/结果盘。
2. **扩池（空闲借用）= `grj-megatron-32card-0716`**（2 pod × ~16）。  
   **仅当目标 pod 无对方训练**时可跑；落盘仍 `yinjinrun.p-huawei`。  
   对方进程再现 → **立刻停我们的作业让路**。不删 vcjob、不写对方盘。  
   **不要默认走 grj**——只有主池被占（如 dyno）或用户明示时才覆盖。
3. **空 = 目标 pod 内没有活训练**（无活 `torchrun` / megatron）。僵尸可忽略。
4. **仍禁止**：`a3-megatron-*`（张文胜）；写宋 AFS / `geruijun` / `grj-shared-log-ckpt`。
5. **落盘**：结果 → `results/ascend-ais/` + AFS `yinjinrun.p-huawei`。  
   - yysong：常有 `/data/yinjinrun.p-huawei`（可选）；默认仍可用 AFS weight-share  
   - grj：**无** `/data/yinjinrun.p-huawei` → 用  
     `POD_BUNDLE`（可读 `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`）+  
     `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`
6. **跳板 kubectl**（PATH 无 kubectl）：

```bash
K=/root/.cache/volcano/kubectl/kubectl
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
```

## 占卡壳登记

### yysong · 64（主池 · 默认）

| pod | 节点（2026-07-24） | 卡 | 建议池 |
|-----|-------------------|----|--------|
| `yysong-master-0` | `host-10-140-217-47` | 16 | Dose / Case Probing |
| `yysong-worker-0` | `host-10-140-217-37` | 16 | **Pillar C** |
| `yysong-worker-1` | `host-10-140-217-48` | 16 | `pool-gh` |
| `yysong-worker-2` | `host-10-140-217-7` | 16 | `pool-xpu` / 机动 |

### grj-megatron-32card-0716 · 32（空闲借用 · 备选）

| pod | 卡 | 角色（仅 IDLE 时） |
|-----|----|-------------------|
| `grj-megatron-32card-0716-master-0` | ~16 | Dose / Case 备选 |
| `grj-megatron-32card-0716-worker-0` | ~16 | Pillar C 备选 |

镜像：与 yysong 同族 mindspeed/llm_test；pullSecret 以壳内已有为准。

## 默认同时刻配额（Dose + C · 2026-07-27）

| 角色 | 卡 | 落点（优先） | 备选 |
|------|-----|--------------|------|
| Dose / Case ≤1 | 16 | **yysong-master-0** | grj-m0（IDLE + 让路） |
| Pillar C ≤1 | 16 | **yysong-worker-0** | grj-w0（IDLE + 让路） |
| Greyhound ≤1 | ≤16 | **yysong-worker-1** | yysong-worker-2 |
| XPUTimer ≤1 | ≤16 | **yysong-worker-2** | 与 GH 错峰 |
| Loop 父 | 0 | — | — |

满配峰值约 **48 卡**（Dose+C+GH）；XPU 与 GH 错峰。  
`yysong` 上若有他人 `dyno*` / 满载训练：**不要抢**，改走 grj 备选或等空档。

标签 `yjr-as-c-*` / `yjr-as-b-*` 只用于 **run_id / 结果目录**，不是新建 vcjob 名。

## Probing wheel / Rust（铁律 · 2026-07-26）

**不要在 hold pod 里 rustup / 删 toolchain 重装 / 裸拉 crates.io**——集群 egress 极慢（P-FIX 已踩坑）。  
真相源：[**`BUILD_WHEEL.md`**](BUILD_WHEEL.md)（复用 → Mac 摆渡 → 可选反代）。

## 检查清单

- [ ] SYY kube + `JUMP_KUBECTL`  
- [ ] 目标 pod `pgrep` IDLE（或只清**自己的**残留）  
- [ ] 优先 yysong；仅备选才进 grj（确认无对方训练；结果写 weight-share）  
- [ ] 结果写 `ascend-ais` / `yinjinrun.p-huawei`  
- [ ] 不改坏沐曦 dose / 共享脚本默认  
- [ ] **仍不碰 a3-megatron-***  
- [ ] 编/装 probing：**先读** [`BUILD_WHEEL.md`](BUILD_WHEEL.md)；禁 pod 内 rustup 重装  

## 编 wheel（摘要）

详见 [`BUILD_WHEEL.md`](BUILD_WHEEL.md)。集群 egress 下大文件极慢；**本机 `:7897` → scp → 跳板 → kubectl cp**。已有 `wheels/*.whl` 优先只重装；**禁止** `rm -rf` toolchain 再 `rustup install`。

## 登记模板

```text
mode: hold-exec
hold_job_primary: yysong
hold_job_borrow:  grj-megatron-32card-0716   # IDLE only; yield if owner returns
kubectl_on_jump: /root/.cache/volcano/kubectl/kubectl
pool-dose:  pods=yysong-master-0  world=16
pool-c:     pods=yysong-worker-0  world=16
pool-gh:    pods=yysong-worker-1
pool-xpu:   pods=yysong-worker-2
afs_env:
  POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
  POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
never:      a3-megatron-*, 写宋 AFS /afs-a3-241ceshi-shared/yysong, geruijun/*, grj-shared-log-ckpt, 删对方 vcjob
```
