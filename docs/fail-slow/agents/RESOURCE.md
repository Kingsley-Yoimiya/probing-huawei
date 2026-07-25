# 资源划分（yysong 64 卡 · hold-exec）

> 具体 node / pod 填进 [`../ledger.md`](../ledger.md) §1。  
> 与沐曦隔离、三问见 [`CONCERNS.md`](CONCERNS.md)。

## 硬原则（2026-07-24 纠正）

1. **我们管的 64 卡 = `yysong` vcjob**（submitter=`songyiyang.p`，4 pod × 16 Ascend910）。  
   借 SYY kube 就是为了进这套作业；**直接在 `yysong-*` pod 里 `kubectl exec` 跑实验**。  
   **不要**新建抢调度的 vcjob（集群 allocatable 常被占满）；**不要**因「空闲=0」BLOCKED。
2. **空 = `yysong` 壳内没有活训练**（无活 `torchrun` / megatron 等）。僵尸可忽略。
3. **禁止碰** 别人的作业：`a3-megatron-*`（张文胜）、`grj-megatron-*`（葛瑞君）——**不是我们的壳**。
4. **落盘仍归自己**：结果 → `results/ascend-ais/` + AFS `yinjinrun.p-huawei`。  
   **禁止**写 `/afs-a3-241ceshi-shared/yysong` 或宋一扬家目录；只借卡面，不借他的盘。
5. **跳板 kubectl**（PATH 无 kubectl）：

```bash
K=/root/.cache/volcano/kubectl/kubectl
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
```

## 占卡壳登记（yysong · 64）

| pod | 节点（2026-07-24） | 卡 | 建议池 |
|-----|-------------------|----|--------|
| `yysong-master-0` | `host-10-140-217-47` | 16 | `pool-case`（优先） |
| `yysong-worker-0` | `host-10-140-217-37` | 16 | Case 扩到 2 机时用 / 或 baseline |
| `yysong-worker-1` | `host-10-140-217-48` | 16 | `pool-gh` |
| `yysong-worker-2` | `host-10-140-217-7` | 16 | `pool-xpu` |

镜像：`ccr-yangxiaolei/mindspeed-llm:…-2.3.0-a3-arm`（pullSecret `card-screen`）。  
默认 Case **16 卡** = 单 pod；需要 2×8 时用 master + 一个 worker。Baseline 用另外的 worker，**不挡 Case**。

## 同时刻配额

| 角色 | 卡 | 落点 |
|------|-----|------|
| Case formal ≤1 | 16 | `yysong-master-0`（± worker-0） |
| Greyhound ≤1 | ≤16 | `yysong-worker-1` |
| XPUTimer ≤1 | ≤16 | `yysong-worker-2` |
| Loop 父 | 0 | — |

标签 `yjr-as-c-*` / `yjr-as-b-*` 只用于 **run_id / 结果目录**，不是新建 vcjob 名。

## 检查清单

- [ ] SYY kube + `JUMP_KUBECTL`  
- [ ] 目标是 `yysong-*`，不是 a3/grj  
- [ ] 结果写 `ascend-ais` / `yinjinrun.p-huawei`，不写宋盘  
- [ ] 发射前 `pgrep` 确认目标 pod IDLE（或只清自己残留）  
- [ ] 不改坏沐曦 dose / 共享脚本默认  

## 登记模板

```text
mode: hold-exec
hold_job: yysong
kubectl_on_jump: /root/.cache/volcano/kubectl/kubectl
pool-case:  pods=yysong-master-0[,yysong-worker-0]  world=16
pool-gh:    pods=yysong-worker-1
pool-xpu:   pods=yysong-worker-2
never:      a3-megatron-*, grj-megatron-*, 宋 AFS
```
