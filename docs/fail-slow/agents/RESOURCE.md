# 资源划分（yysong 64 + grj 空闲借用 32）

> 具体 node / pod 填进 [`../ledger.md`](../ledger.md) §1。  
> 与沐曦隔离、三问见 [`CONCERNS.md`](CONCERNS.md)。

## 硬原则（2026-07-25 修订）

1. **主池 64 卡 = `yysong` vcjob**（submitter=`songyiyang.p`，4 pod × 16）。  
   借 SYY kube 进壳；**直接 `kubectl exec`**。不要新建抢调度的 vcjob。
2. **扩池（空闲借用）= `grj-megatron-32card-0716`**（2 pod × ~16）。  
   **仅当目标 pod 无对方训练**时可跑我们的实验；落盘仍 `yinjinrun.p-huawei`。  
   对方进程再现 → **立刻停我们的作业让路**。不删 vcjob、不写对方盘。
3. **空 = 目标 pod 内没有活训练**（无活 `torchrun` / megatron）。僵尸可忽略。
4. **仍禁止**：`a3-megatron-*`（张文胜）；写宋 AFS / `geruijun` / `grj-shared-log-ckpt`。
5. **落盘**：结果 → `results/ascend-ais/` + AFS `yinjinrun.p-huawei`。  
   - yysong：常有 `/data/yinjinrun.p-huawei`  
   - grj：**无** `/data/yinjinrun.p-huawei` → 用  
     `POD_BUNDLE`（可读 `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`）+  
     `POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`
6. **跳板 kubectl**（PATH 无 kubectl）：

```bash
K=/root/.cache/volcano/kubectl/kubectl
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
```

## 占卡壳登记

### yysong · 64（主池）

| pod | 节点（2026-07-24） | 卡 | 建议池 |
|-----|-------------------|----|--------|
| `yysong-master-0` | `host-10-140-217-47` | 16 | Dose Probing（若未被 dyno 占） |
| `yysong-worker-0` | `host-10-140-217-37` | 16 | Pillar C（若空） |
| `yysong-worker-1` | `host-10-140-217-48` | 16 | `pool-gh` |
| `yysong-worker-2` | `host-10-140-217-7` | 16 | `pool-xpu` / 机动 |

### grj-megatron-32card-0716 · 32（空闲借用）

| pod | 卡 | 今晚默认角色 |
|-----|----|--------------|
| `grj-megatron-32card-0716-master-0` | ~16 | **Dose Probing**（Quiet/Masked） |
| `grj-megatron-32card-0716-worker-0` | ~16 | **Pillar C**（Pilot→Runner） |

镜像：与 yysong 同族 mindspeed/llm_test；pullSecret 以壳内已有为准。

## 今晚同时刻配额（Dose + C · 2026-07-25）

| 角色 | 卡 | 落点（优先） | 备选 |
|------|-----|--------------|------|
| Dose Probing ≤1 | 16 | **grj-master-0** | yysong-master-0（若 IDLE） |
| Pillar C ≤1 | 16 | **grj-worker-0** | yysong-worker-0（若 IDLE） |
| Greyhound ≤1 | ≤16 | **yysong-worker-2**（当前常 IDLE） | yysong-worker-1 |
| XPUTimer ≤1 | ≤16 | 排在 GH 后同 pod，或等 yysong 空档 | — |
| Loop 父 | 0 | — | — |

满配峰值约 **48 卡**（Dose+C+GH）；XPU 与 GH 错峰。  
`yysong` 上若有 `dyno27-*`：**不要抢**，改走 grj / w2。

标签 `yjr-as-c-*` / `yjr-as-b-*` 只用于 **run_id / 结果目录**，不是新建 vcjob 名。

## Probing wheel / Rust（铁律 · 2026-07-26）

**不要在 hold pod 里 rustup / 删 toolchain 重装 / 裸拉 crates.io**——集群 egress 极慢（P-FIX 已踩坑）。  
真相源：[**`BUILD_WHEEL.md`**](BUILD_WHEEL.md)（复用 → Mac 摆渡 → 可选反代）。

## 检查清单

- [ ] SYY kube + `JUMP_KUBECTL`  
- [ ] 目标 pod `pgrep` IDLE（或只清**自己的**残留）  
- [ ] grj：确认无对方训练；结果写 weight-share，不写对方盘  
- [ ] 结果写 `ascend-ais` / `yinjinrun.p-huawei`  
- [ ] 不改坏沐曦 dose / 共享脚本默认  
- [ ] **仍不碰 a3-megatron-***  
- [ ] 编/装 probing：**先读** [`BUILD_WHEEL.md`](BUILD_WHEEL.md)；禁 pod 内 rustup 重装  

- [ ] **编/装 Probing wheel**：读 [`BUILD_WHEEL.md`](BUILD_WHEEL.md)——禁止 pod 内 rustup 重装/公网下工具链；本机 Clash 摆渡  

## 编 wheel（摘要）

详见 [`BUILD_WHEEL.md`](BUILD_WHEEL.md)。集群 egress 下大文件极慢；**本机 `:7897` → scp → 跳板 → kubectl cp**。已有 `wheels/*.whl` 优先只重装；**禁止** `rm -rf` toolchain 再 `rustup install`。

## 登记模板

```text
mode: hold-exec
hold_job_primary: yysong
hold_job_borrow:  grj-megatron-32card-0716   # IDLE only; yield if owner returns
kubectl_on_jump: /root/.cache/volcano/kubectl/kubectl
pool-dose:  pods=grj-megatron-32card-0716-master-0  world=16
pool-c:     pods=grj-megatron-32card-0716-worker-0  world=16
pool-gh:    pods=yysong-worker-2
pool-xpu:   pods=yysong-worker-2 (after GH) | yysong-worker-1 if IDLE
grj_env:
  POD_BUNDLE=/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle
  POD_RESULTS=/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais
never:      a3-megatron-*, 宋 AFS, geruijun/*, grj-shared-log-ckpt, 删对方 vcjob
```
