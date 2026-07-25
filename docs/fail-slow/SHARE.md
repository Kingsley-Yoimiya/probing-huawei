# 华为昇腾 Fail-Slow · 对外入口（无 myportal）

> **myportal 是维护者私有编排仓，不对外开放，也不是运行依赖。**  
> 外人只要：**① 两份公开代码仓** + **② 集群/跳板访问权限** + **③ 自备 kube**，即可跑 Case / Baseline。

密钥不进仓；kube 正文只放本机 `~/.kube/` 或跳板 `/tmp/`。

---

## 0. 你需要什么

| 需要 | 不需要 |
|------|--------|
| `probing-huawei`（台账 + NPU Probing + `scripts/fail-slow/`） | **myportal** |
| `probing-test`（`platform/ascend` 竞品适配与共享编排） | 维护者本机路径 / vault zip |
| 跳板 SSH（如 `ais-cf3e61a5`）+ SYY kube 进 `vc-a3-241ceshi` | 写宋一扬 AFS / 碰 a3·grj 作业 |
| 落盘前缀权限：`yinjinrun.p-huawei`（或你被授权的等价前缀） | |

建议目录（同级 clone，env.sh 会自动找到）：

```text
~/Codespace/
  probing-huawei/     # git@github.com:Kingsley-Yoimiya/probing-huawei.git
  probing-test/       # git@github.com:Kingsley-Yoimiya/probing-test.git
```

若 probing-test 不在同级：

```bash
export FS_SHARED_SCRIPTS=/path/to/probing-test/scripts/fail-slow
```

---

## 1. 台账与规则（本仓）

| 文件 | 说明 |
|------|------|
| [`README.md`](README.md) | 华为侧文档总入口 |
| [`IDENTITY.md`](IDENTITY.md) | kube / 跳板 / 落盘约定（无密钥） |
| [`rules.md`](rules.md) | 方法论；公平对照 §三·五A |
| [`ledger.md`](ledger.md) | 门禁、剂量、已跑 case、Baseline 公平性记录 |
| [`CASE_QUEUE.md`](CASE_QUEUE.md) | 27-case 排期 |
| [`agents/`](agents/README.md) | 双轨任务卡（Case / Greyhound / XPUTimer） |

---

## 2. 竞品适配代码（probing-test）

```text
probing-test/scripts/fail-slow/platform/ascend/
  README.md  BASELINE_PORTING.md  SYMBOL_MAP.md
  greyhound/   # collect_min.c、collect_seq.py、S3/S4
  xputimer/    # xpu_timer_ascend_hook.cc、S3/S4
```

公平性修正（2026-07-25）：Greyhound 喂真实 per-rank 序列 + C0 假阳性对照；XPUTimer 分列自主 flags vs 跨-run 中位比。

---

## 3. 结果落在哪（可覆盖）

| 位置 | 默认 | 说明 |
|------|------|------|
| 本机备份 | `$FS_HUAWEI_ROOT/results/ascend-ais/` | `source scripts/fail-slow/env.sh` 后 `LOCAL_RESULT_ROOT_BASE` |
| Pod 真盘 | `/data/yinjinrun.p-huawei/results/ascend-ais/` | hold-exec 写入；回拉到本机 |
| AFS 前缀 | `/afs-a3-weight-share/yinjinrun.p-huawei/` | 部分节点假挂载，以 pod `/data` 为准 |

覆盖示例：

```bash
export LOCAL_RESULT_ROOT_BASE=$HOME/ascend-ais-results
export FS_SHARED_SCRIPTS=$HOME/src/probing-test/scripts/fail-slow
source probing-huawei/scripts/fail-slow/env.sh
```

历史对照摘要（STATUS / S4_VERDICT）可放在分享 zip；全量 jsonl 在授权机器上从 pod 回拉即可。

---

## 4. 最小开跑

```bash
git clone git@github.com:Kingsley-Yoimiya/probing-huawei.git
git clone git@github.com:Kingsley-Yoimiya/probing-test.git
# 自备：~/.kube/config-vc-a3-241ceshi-songyiyang.yaml  → 跳板 /tmp/...
cd probing-huawei
source scripts/fail-slow/env.sh
# 读 docs/fail-slow/{IDENTITY,rules,ledger,agents/RESOURCE}.md
# Case：scripts/fail-slow/hold_exec_run_case.sh …
# Baseline：$FS_PLATFORM_ASCEND/greyhound|xputimer/ …
```

原则：有机器权限就能跑；**禁止**依赖未公开的门户仓路径。
