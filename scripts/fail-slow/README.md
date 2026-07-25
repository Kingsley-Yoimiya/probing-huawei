# scripts/fail-slow（华为薄层）

本目录**不复制**整套沐曦编排（避免双份漂移）。**不依赖 myportal。**

| 文件 | 作用 |
|------|------|
| `env.sh` | 结果根、AFS/DATA、SYY kube、hold-exec pods、定位 `probing-test` |
| `dose_recipes.yaml` | 昇腾剂量 |
| `hold_exec_run_case.sh` | yysong hold-exec 发射（经跳板 kubectl） |
| `sync_kube_to_jump.sh` | 本机 SYY kube → 跳板 `/tmp/...` |
| `probe_gate.sh` | 门禁快检（can-i / yysong IDLE） |

对外总入口：`docs/fail-slow/SHARE.md` · 身份：`docs/fail-slow/IDENTITY.md`。

## 最小用法（两仓同级）

```bash
# ~/Codespace/{probing-huawei,probing-test}
cd ~/Codespace/probing-huawei
source scripts/fail-slow/env.sh
bash scripts/fail-slow/sync_kube_to_jump.sh
bash scripts/fail-slow/probe_gate.sh

CASE_ID=P3-EXT-A DOSE=loud PHASE=pilot \
  ABC_CONFIGS=C0_baseline,C1_inject_none \
  bash scripts/fail-slow/hold_exec_run_case.sh
```

训练脚本：`$FS_PLATFORM_ASCEND/train_bench_probe_npu.py`。  
本机结果：`$LOCAL_RESULT_ROOT_BASE/<run_id>/`（默认本仓 `results/ascend-ais/`）。  
Pod：`/data/yinjinrun.p-huawei/results/ascend-ais/`。

`probing-test` 不在同级时：`export FS_SHARED_SCRIPTS=/path/to/probing-test/scripts/fail-slow`。
