# 华为昇腾 Fail-Slow · 对外入口（台账 / 规则 / 代码 / 竞品）

> 给协作者：先读本页，再按链接进仓。密钥不进仓；集群 kube 只在本机 vault。

## 1. 三仓分工

| 仓 | Remote | 放什么 |
|----|--------|--------|
| **probing-huawei** | `git@github.com:Kingsley-Yoimiya/probing-huawei.git` | 华为侧 **rules / ledger / CASE_QUEUE / Agent 任务卡**；NPU Probing 包；薄包装 `scripts/fail-slow/` |
| **probing-test** | `git@github.com:Kingsley-Yoimiya/probing-test.git` | **Baseline 适配源码**（`platform/ascend/{greyhound,xputimer}/`）、共享编排、符号表 |
| **myportal** | `git@gitee.com:yinjinrun/myportal.git` | 本机结果根 `results/ascend-ais/`、身份 shortcut、跨仓挂载 `project/registry.yaml` |

本机挂载（myportal 内点文件）：

- `project/probing-huawei` → `~/Codespace/probing-huawei`
- `project/probing-test` → lab-workspace 内 probing-test submodule 路径

## 2. 台账与规则（华为副本）

入口：[`README.md`](README.md)

| 文件 | 说明 |
|------|------|
| [`rules.md`](rules.md) | 方法论红线 / 控变 / D0–D5；**公平对照**见 §三·五A |
| [`ledger.md`](ledger.md) | 门禁、剂量、已跑 case、Baseline S4 与公平性修正记录 |
| [`CASE_QUEUE.md`](CASE_QUEUE.md) | 27-case 排期（第一梯队 6/6 SCORED；其余 PENDING / SKIP_PERM） |
| [`agents/`](agents/README.md) | 双轨 Loop：Case 16 卡 / Baseline 另池 |
| [`agents/BASELINE_COMMON.md`](agents/BASELINE_COMMON.md) | 竞品适配阶段机 S0–S6；禁止改对手判据抬分 |
| [`agents/BASELINE_GREYHOUND.md`](agents/BASELINE_GREYHOUND.md) | Greyhound 任务卡 |
| [`agents/BASELINE_XPUTIMER.md`](agents/BASELINE_XPUTIMER.md) | XPUTimer 任务卡 |

## 3. Baseline 适配代码（竞品）

仓内路径（**probing-test**）：

```text
scripts/fail-slow/platform/ascend/
  README.md / BASELINE_PORTING.md / SYMBOL_MAP.md / COMPAT_MATRIX.md
  greyhound/          # HCCL collect_min、collect_seq、S3/S4、Redis
  xputimer/           # ascend hook .cc、build、S3/S4 verdict
  train_bench_probe_npu.py
```

关键公平性修正（2026-07-25 审查后）：

- Greyhound：`collect_seq.py` 喂**真实 per-rank 序列** + C0 假阳性对照（不再人造 `i%4` / 恒 0）
- XPUTimer：S4 分列 **自主 flags** vs **跨-run 中位比**；不再误标 `autonomous`

## 4. 实验结果与竞品对照

本机 / myportal：

```text
results/ascend-ais/
  INDEX.md                          # Case 跑分索引
  <run_id>/                         # Probing C0/C1/C2
  baseline/greyhound/STATUS.md      # 阶段与公平性说明
  baseline/greyhound/NOTES.md
  baseline/greyhound/yjr-as-b-gh-s4-*/S4_VERDICT.md
  baseline/xputimer/STATUS.md
  baseline/xputimer/NOTES.md
  baseline/xputimer/yjr-as-b-xpu-s4-*/S4_VERDICT.md
```

当前对照 case：`P3-EXT-A` Loud（host CPU）；两者能力范围内均 **无自主咬合**；剂量经 step_ms 核对 OK。

## 5. 最小配置（他人复现）

1. clone 上述三仓（或至少 probing-huawei + probing-test）。  
2. 身份：借 `songyiyang.p` 进 `vc-a3-241ceshi`；**落盘**只用 `yinjinrun.p-huawei`（见 myportal `config/identity/songyiyang.p-huawei.yaml`）。  
3. `source probing-huawei/scripts/fail-slow/env.sh`  
4. Baseline：读 `BASELINE_PORTING.md` → 按 STATUS 阶段机；S4 用 `s4_p3exta_contrast.sh`。  
5. **禁止**写宋 AFS / 碰 a3·grj 作业；结果回拉 `results/ascend-ais/`。

## 6. 压缩包

同内容可打成分享包（无密钥、无巨型 jsonl 全量时可只要摘要）：见 myportal  
`tmp/ascend-failslow-handoff-YYYYMMDD.zip`（由本机脚本生成，不进 git）。
