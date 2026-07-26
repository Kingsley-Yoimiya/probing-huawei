# 身份与通道（无密钥正文）

> 外人只需：kube 文件自备 + 跳板可 SSH + 落盘前缀可写。  
> **不依赖 myportal**；下列路径是约定名，可按环境改 env。

## 集群

| 项 | 约定 |
|----|------|
| 集群 | `vc-a3-241ceshi`（华为 AIS） |
| 进集群身份 | 借用 `songyiyang.p`（仅访问） |
| 落盘身份 | **`yinjinrun.p-huawei`**（不写宋盘、不写 `/afs-a3-241ceshi-shared/yysong`） |
| 跳板 | `ais-cf3e61a5`（主机名以你方库存为准） |
| kubectl（跳板） | `/root/.cache/volcano/kubectl/kubectl` |

## kube 放置

```bash
# 本机（示例文件名）
~/.kube/config-vc-a3-241ceshi-songyiyang.yaml

# 同步到跳板后
export KUBECONFIG=/tmp/config-vc-a3-241ceshi-songyiyang.yaml
```

`source scripts/fail-slow/env.sh` 默认指向上述本机路径；可用 `KUBECONFIG_SYY` 或 `FS_KEEP_KUBECONFIG=1` 覆盖。

## hold-exec 池（当前战役）

| 用途 | Pod（在 `yysong` 作业内） |
|------|--------------------------|
| Case 16 卡 | `yysong-master-0` |
| Greyhound | `yysong-worker-1` |
| XPUTimer | `yysong-worker-2` |

作业前缀标签：`yjr-as-c-*`（Case）、`yjr-as-b-*`（Baseline）。  
**禁止**碰 `a3-megatron-*`。  
**`grj-megatron-32card-0716`**：允许空闲借用（见 `agents/RESOURCE.md`）；不写对方盘、不删对方 vcjob。

## 落盘

| 侧 | 路径 |
|----|------|
| Pod | `/data/yinjinrun.p-huawei/results/ascend-ais/<run_id>/` |
| 本机 | `$FS_HUAWEI_ROOT/results/ascend-ais/`（env 默认；可 `LOCAL_RESULT_ROOT_BASE` 改） |

跑完立刻回拉本机；远端盘不可靠。
