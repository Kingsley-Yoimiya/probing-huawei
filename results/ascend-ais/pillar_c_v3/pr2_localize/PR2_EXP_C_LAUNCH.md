# PR-2 实验 C · 追溯窗复现（P1-SW-C）发射记录

| 字段 | 值 |
|------|-----|
| parent | `20260728_211312-pillar-c-v3-pr2-exp-c-p1swc` |
| arm | `20260728_211312-pillar-c-v3-pr2-exp-c-p1swc-upgrade_rate_1.0` |
| pod | `grj-megatron-32card-0716-worker-0`（grj-w0，主池 yysong-w0 rank15 stuck 让路） |
| case | P1-SW-C loud · GT victim rank=7 · inject_kind=2c (n=1024 every=1 fallback_s=0.6) |
| ITERS | 1000 · inject [100,300] |
| scope | localize + **15s** 时基降回 |
| hang_max | **480s**（8min） |
| 常驻 | `on,rate=0` |
| 全量臂 | REUSE v2 P1-SW-C `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/20260726_012627-pillar-c-p1-sw-c-loud/full_fidelity` |
| B8 gates | STEP_AGG=**avg** STEP_WINDOW=**100** NO_PROG_KILL_S=**90** HCCL_EXEC_TIMEOUT=**600** |
| B6 gates | COMM_LAZY=1 · STEP_TIMING_LAZY=0 · PRUNE=1 · DRY=0 |
| 目标（handbook §2.4） | W* first enough=Y @ W=100（不迟于 200）· duration_spike step=X dur_s>=0.5 module=Y |
| 参考 | v2 E1_off P1-SW-C W*=**100** (spike@238 AdamW dur≈0.71s) |
| v2 正式跑失败 | `20260726_173830-pillar-c-e1-p1-sw-c-loud` (SET 键名错 → 未升详 → NO_W_STAR) |

## 发射
`_prep/launch_exp_c.sh` @ 2026-07-28T21:13:12+08:00
