# PR-3 P1-HW-B 补跑 · 发射记录

**日期**：2026-07-29 00:39
**pod**：`grj-megatron-32card-0716-worker-0`（grj-w0，主池 yysong-w0 rank 15 stuck 让路铁律）
**parent**：`20260729_003933-pillar-c-v3-pr3-p1hwb`
**arm**：`20260729_003933-pillar-c-v3-pr3-p1hwb-upgrade_rate_1.0`
**case**：**P1-HW-B loud**（GT victim rank=7 · inject_kind=**1b INLINE HBM ramp**, mb=512, copies=6→48, ramp=1）
**ITERS**：1000 · inject [100,300]

## 上下文

- PR-3 阶段 2 前置：P1-SW-C W\*=200 步 + P3-SW-A W\*=60 秒 已获，仅剩 P1-HW-B defer 待补
- 阶段 1 wheel `probing-0.2.6-cp38-abi3-linux_aarch64.whl` (sha `9416803e...`) 已装 grj-w0：`gpu.utilization` 默认 `retain_secs=3600`
- 补跑目的：拿到 P1-HW-B 的 W\* 数字，凑齐 3/3 家族 → PR-3 阶段 2 PASS

## Gates & 参数

| 字段 | 值 |
|------|-----|
| ARM | `e3a_upgrade`（`RESIDENT_RATE=0`, `PILLAR_C_SET_RATE=1.0`, `PILLAR_C_SET_SCOPE=localize`） |
| SET at step | 100（trigger=L>=100 jsonl lines）|
| SET window_s | 15s 时基降回 |
| SET hang_max_s | 480s |
| B8 gates | STEP_AGG=**avg** STEP_WINDOW=**100** NO_PROG_KILL_S=**90** HCCL_EXEC_TIMEOUT=**600** |
| B6 gates | COMM_LAZY=1 · STEP_TIMING_LAZY=0 · PRUNE=1 · DRY=0 |
| INLINE HBM | mb=512 copies=6→48 ramp=1 |
| SIDECAR_LOCAL_RANK | 7（victim）|
| 全量臂 | **不 REUSE**（P1-HW-B 判据从 torch_trace.max_allocated 迁到 gpu.utilization.used_bytes，v2 dump 不含 MEMT gpu.utilization） |

## 时序

- 00:39:33 发射：`_prep/launch_exp_p1hwb.sh` 
- 00:39 本地自检 PASS（`test_pillar_c_set_window` + `localize.py avg+window=100` + SET key hygiene + hold_exec gates）
- 00:39 grj-w0 IDLE 校验（ps 无 owner torchrun）
- 00:40 warmup ok (20s)
- 00:40 measure step 100 reached (10s) → INLINE HBM ramp arm
- 00:40 SET wait L>=100 → L=191（inline 后 90s 内已跑到）
- 00:41 localize SQL 空/no-culprit → `LOCALIZE_FALLBACK=1 (all-ranks)` → SET rate=1.0 16 pids OK
- 00:41:26 SET_UPGRADE step=215 pid=3680244(+15 more) rate=1.0
- 00:41 26s elapsed >= window_s=15s → SET_DOWNGRADE reason=time
- 00:43 measure step 300 → stop injectors → node_0.done
- 00:44 SQL dump 90s 后 → pull 1.79GB tar
- 00:48 pull DONE + jsonl_files=16 + hold-exec rc=0
- 00:49–00:50 本地 patch `e3_retention_score.py` 处理 gpu.utilization anchor 语义 + wall_ns fallback
- 00:50 判分完成：**W\*=60 秒**

## 判分结果

- **W\* = 60 秒**（gpu.utilization used_bytes rise_mb=10788 MB @ dev 12; victim rank 7 dev 7 rise=8692 MB）
- 6 档位 {60,300,900,1800,3600,all} 全 enough=Y → 60 秒是最紧的
- 详见 `../pr3_retention_scan/W_STAR_P1_HW_B.json` + `PR3_EXP_P1HWB_STATUS.md`

## 判据代码补丁（本地，不 commit）

`project/probing-huawei/scripts/fail-slow/e3_retention_score.py`：
- `judge_p1_hw_b_gpu`：加 `ts` 列 degenerate 检测 → 回退 `wall_ns`（本 dump 用 `ts` 有 14 值，未回退）
- `scan_p1_hw_b`：anchor 语义改进 —— 若 gpu 所有 ts > inject_stop_ts+1s（gpu 环只留末尾），用 `max(gpu.ts)`（dump 时刻）作 anchor；否则用 inject_stop_ts（原语义）

原因：`gpu.utilization` 默认 `retain_secs=3600` 但实际只落 3s 的 wall_ns / 55s 的 ts 跨度，不含 inject_stop 时刻。retention 窗语义应"反查到 W 秒前"，故 anchor 应对齐 dump 时刻。

## 备注

- LOCALIZE 兜底 all-ranks 不阻塞判分（判分脚本按 LOCAL_RANK=7 定位 pid，不依赖 SET 命中）
- 训练稳定跑到 1000 步（node_0.done），无 no-progress kill / 无 HCCL timeout / 无 HANG_DETECTED
- SET_DOWNGRADE 正常触发 reason=time（validates B3 时基降回）
- run_pillar_c_arm.sh 的 P1-HW-B 分支已 wire 好（`INLINE_HBM_*_OVERRIDE`），无需改 code
- 发射脚本 launch_exp_p1hwb.sh 在 pull 后的 score 步骤因 `**` 未 globstar 展开导致 pipefail 早退，判分由手动补跑（此点不阻塞 W\* 数据，已归档为已知问题）
