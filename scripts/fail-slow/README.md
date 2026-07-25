# scripts/fail-slow（华为薄层）

本目录**不复制**整套沐曦编排（避免双份漂移）。职责：

| 文件 | 作用 |
|------|------|
| `env.sh` | SYY kube、结果根 `ascend-ais`、AFS、`FS_JOB_PREFIX`、指向共享脚本 |
| `dose_recipes.yaml` | 昇腾剂量（P3-EXT-A loud 已标定） |
| `hold_exec_run_case.sh` | yysong hold-exec 发射（经跳板 kubectl） |
| `sync_kube_to_jump.sh` | 本机 SYY kube → 跳板 `/tmp/...` |
| `probe_gate.sh` | 门禁快检（can-i / yysong IDLE） |

## hold-exec（推荐 · yysong）

```bash
cd project/probing-huawei
source scripts/fail-slow/env.sh
bash scripts/fail-slow/sync_kube_to_jump.sh
bash scripts/fail-slow/probe_gate.sh   # 确认 yysong-master-0 IDLE

CASE_ID=P3-EXT-A DOSE=loud PHASE=pilot \
  ABC_CONFIGS=C0_baseline,C1_inject_none \
  bash scripts/fail-slow/hold_exec_run_case.sh
```

训练脚本：`platform/ascend/train_bench_probe_npu.py`（经 `FS_PLATFORM_ASCEND`）。  
结果：`results/ascend-ais/<run_id>/`；pod AFS：`/data/yinjinrun.p-huawei/`。

## 共享编排怎么用

```bash
cd project/probing-huawei   # 或 ~/Codespace/probing-huawei
source scripts/fail-slow/env.sh
bash scripts/fail-slow/sync_kube_to_jump.sh
bash scripts/fail-slow/probe_gate.sh

# 沐曦 raw-pod 路径仍可用共享脚本；昇腾 hold-exec 优先用上面入口
# export LOCAL_RESULT_ROOT=$LOCAL_RESULT_ROOT_BASE/<run_id>
# bash "$FS_SHARED_SCRIPTS/run_case_abc.sh" ...
```

平台差分（HCCL env、npu-smi 旁路、baseline 构建）在：

`project/probing-test/scripts/fail-slow/platform/ascend/`

文档：`docs/fail-slow/`。
