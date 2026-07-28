# PR-4 可行性摸底（阶段 0）

**日期**：2026-07-29
**范围**：Pillar C v3 PR-4（多机 × 联邦查询）—— 手册 §4.1.a/4.1.b/4.3
**依据**：handbook 明文 —— *"若多机机时不够，PR-4 跳过；单机 PR-1/2/3 已够撑 outline §5.2.C 头条"*
**结论（先说）**：**DEFER**（本轮跳过 PR-4，主 Loop 收工到战役 SUMMARY）

---

## 1. Pod IDLE 表（vc-a3-241ceshi，2026-07-29）

| Pod | 状态 | 卡数 | pgrep 结果 | 备注 |
|-----|------|------|-----------|------|
| `yysong-master-0` | **IDLE** | 16 | 无 torchrun/megatron；仅 `[python]<defunct>` + `[dynolog]<defunct>` 遗留 | 可进壳；`ASCEND_VISIBLE_DEVICES` 有 16 卡；wheel/`llm_test` 已装 |
| `yysong-worker-0` | **IDLE** | 16 | 无 torchrun/megatron；遗留 defunct python3.10 | **rank 15 stuck 风险**，只 pgrep 不上机 |
| `yysong-worker-1` | **IDLE** | 16 | 无 torchrun；遗留 defunct dynolog + python | 可进壳（GH 家族历史备选） |
| `yysong-worker-2` | **IDLE** | 16 | 无进程；仅遗留 defunct python3.10 | 可进壳（XPU 家族历史备选） |
| `grj-megatron-32card-0716-master-0` | **IDLE** | 16 | 无 torchrun/megatron；仅遗留 defunct python3.10 | 主池占用时的备选空闲壳 |
| `grj-megatron-32card-0716-worker-0` | **IDLE** | 16 | 无 torchrun；遗留 defunct | 32 卡对子完备（配合 master-0） |

**其他可见 pod**：`huawei-64node-200b-dense-finally-*`（64 节点全 Running，14h 起，别人的作业，禁进）；`a3-megatron-32card-master-0/worker-0`（他人 5d15h，禁进）。

**结论**：机时**充裕**（yysong 4 pod + grj 2 pod 全 IDLE，最大 6 pod × 16 卡 = 96 卡可用）——**pod 层面不是瓶颈**。

---

## 2. Federation 现状：**未开通** —— probing daemon 全部未启动

进入 `yysong-master-0` / `grj-megatron-32card-0716-master-0` 实测（`conda activate llm_test`）：

```
$ probing list          → No processes with injected probes found.
$ probing cluster nodes → RuntimeError: Connection refused (os error 111)
$ probing query "..."   → RuntimeError: Connection refused (os error 111)
$ netstat -tlnp         → 仅 sshd:22（无 probing HTTP / Unix socket）
$ pgrep -af probing     → 无
```

**根因**：现有 pod **不常驻 probing daemon**——handbook §4 假设的 `probing -q "SELECT * FROM global.python.comm_collective"` 需要**多个 pod 都跑着 probing daemon** 且**通过 HTTP 互相 discovery**。当前架构是：

- Launch 脚本（`scripts/fail-slow/hold_exec_run_case.sh` L567-568）在 **单 pod** 里 `torchrun --nnodes=1 --nproc_per_node=16 --node_rank=0 --master_addr=<pod_ip>`
- Probing 只是训练进程内的**注入库**（`PROBING=1` env），生命周期 = 训练进程；训练结束后 daemon 也没了
- 联邦发现代码存在（`probing/server/src/torchrun_cluster.rs` — `maybe_start_torchrun_cluster()` 需 `WORLD_SIZE > 1` + `MASTER_ADDR/PORT`），依赖 torch TCPStore 集合点；单 pod nnodes=1 场景 `world_size=16` 触发一节点内 daemon 但**无跨 pod 联邦**

**Rust 代码就绪度**（`probing/core/src/core/federation/`）：文件齐全 — `aggregate_pushdown.rs` / `federated_scan_exec.rs` / `cluster_executor.rs` / `global_catalog.rs` / `global_table.rs`。`PROBING_FANOUT_CONCURRENCY=128` / `PROBING_REMOTE_QUERY_TIMEOUT_SECS=30` env 存在。**代码层面不需要重写 PR-4b（源头过滤）**，需要**上层脚本让 daemon 常驻并让 pod 互相发现**。

---

## 3. 多节点 launch 改动量估计

现状（`hold_exec_run_case.sh`）：默认 `NNODES=1 NPROC=16`，`MASTER_IP=<单 pod IP>`，driver 只在 master-0 起 torchrun。

要跑 handbook §4.3 实验 A（32 rank / 4 节点定位）+ 实验 B（32 rank 联邦对照），需要：

1. **多 pod 协同 launch**（新脚本，**~1.5-3 h**）：
   - 在跳板 `ais-cf3e61a5` 上循环给 N 个 pod 发 `kubectl exec setsid nohup torchrun --nnodes=N --node_rank=X --master_addr=<pod-0 IP> --master_port=<port>`
   - pod-0 是 rendezvous master，其他 pod 是 worker
   - 每个 pod 内 `WORLD_SIZE=N*16 > 1` → `maybe_start_torchrun_cluster()` 自动起 HTTP 联邦 daemon（依赖 torch TCPStore 交换 addr）
2. **Sidecar 咬合改造**（`sidecar_inject_npu.py` + `PILLAR_C_SET_...` 环境，**~1 h**）：
   - 现脚本假设 sidecar 与 victim 同 pod（`SIDECAR_LOCAL_RANK`）；多节点场景要选 pod + rank 双标识
3. **Localize SQL 从 `python.*` 改 `global.*`**（`pillar_c_localize_culprit.py`，**~30 min**）：
   - 现走 `probing -t <pid> query "SELECT ... FROM python.comm_collective"`；PR-4a 改成 `probing cluster query "SELECT rank FROM global.python.comm_collective GROUP BY rank ORDER BY MAX(duration) DESC LIMIT 1"`
   - 需先 `probing cluster nodes` 探活 —— 假设 daemon 都能被发现
4. **联邦源头过滤 4.1.b**（Rust wheel，**~4-8 h + 编 wheel + 分发**）：
   - `aggregate_pushdown.rs` 加 `WITH FILTER <sub-sql>` 识别
   - `federated_scan_exec.rs` 先发判据 SQL、按结果决定是否发主查询
   - **本机需 rustup + cargo + maturin 编 wheel + 分发到 pod**（`install_probing_wheel_on_pod.sh` 已有跳板本地编译流程，仍需 ~1 h wall）

**合计**：4.1.a 单跑 = **~3-5 h**（可勉强今日内）；4.1.b 齐做 = **~10-14 h**（跨天）；再加 8-16 rank 实测 debug**至少 4 h**。

---

## 4. 决策：**DEFER**

**触发条件对照**：
- Federation 通？**未通**（Connection refused，daemon 从未启动，需要新脚本让 daemon 常驻）
- 至少 2 pod IDLE？**满足**（yysong 4 + grj 2 全 IDLE）
- Launch 改动 <2h？**不满足**（最快也要 3 h，含 debug 至少 5 h）

**触发 DEFER**：federation 未开 + launch 大改（新脚本 + sidecar 改造 + wheel 编译）+ 4.1.b Rust 改动跨天 → 超过战役"stretch 30 min–2 h"边界。

**handbook 明文允许**：*"若多机机时不够，PR-4 跳过；单机 PR-1/2/3 已够撑 outline §5.2.C 头条。"* 现况机时不缺（pod 全 IDLE），但**工程复杂度不匹配 stretch 定位**——本轮 PR-1/2/3 已 PASS，收 CAMPAIGN 更保头条数字。

**一句话建议给主 Loop**：**DEFER PR-4，派战役最终 SUMMARY sub-agent 收工**；PR-4 留给下一战役独立立项（把 daemon 常驻脚本 + 4.1.b wheel 各自当独立 PR 分开验收）。

---

## 5. 下轮建议

- **主 Loop**：派 `CAMPAIGN_FINALIZE` sub-agent —— 收 `pillar_c_v3/CAMPAIGN_SUMMARY.md` 末尾"PR-4 状态"从"未开工"改成"DEFER（feasibility 结论见 `pr4_multinode/PR4_FEASIBILITY.md`）"，并把附录 A 离线消融的 DONE_PARTIAL 状态一并收尾。
- **PR-4 独立立项要点**（存档给下战役）：
  1. 先做 daemon 常驻：写 `probing_daemon_launch.sh`，在跳板上给 N 个 pod fanout `torchrun --nnodes=N` 并等 `probing cluster nodes` 探活成功
  2. 再改 `pillar_c_localize_culprit.py` 用 `global.*`（4.1.a，pure Python，无 wheel 依赖）
  3. 最后做 4.1.b（Rust `WITH FILTER` 下推 + wheel 编 + 分发）—— 单独一 PR
