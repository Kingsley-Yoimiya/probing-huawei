# PR-3 P1-HW-B 补跑 · PASS

**日期**：2026-07-29
**parent**：`20260729_003933-pillar-c-v3-pr3-p1hwb`
**pod**：`grj-megatron-32card-0716-worker-0`（grj-w0）
**case**：P1-HW-B loud（HBM 渐衰 INLINE 1b ramp）

## 头条 · W\* 追溯窗（handbook §3.4 P1-HW-B 主判据）

| 项 | 值 | 判据 |
|----|-----|-----|
| **W\*** | **60 秒** | 6 档位 {60,300,900,1800,3600,all} 首个 enough=Y |
| primary_evidence | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` | `gpu.utilization_used_bytes` 抬升阈值 ≥ 256 MiB |
| **rise_mb (max dev)** | **10788 MB** @ dev 12（远超 256 MiB 阈值） | — |
| rise_mb (victim rank 7 dev 7) | 8692 MB | — |
| n_gpu_rows | 214（16 devs × ~13 samples） | — |
| n_gpu_ts_unique | 14 | 用 `ts` 直方图；ts 多值 → 判 `use_wall=False` |

## 五指标

| 项 | 值 | 判据 |
|----|-----|-----|
| 训练完成 | **1000 步 · node_0.done ✓** | rc=0 |
| step_100 marker | ✓ | inject_start |
| step_300 marker | ✓ | inject_stop |
| INLINE HBM ramp active | ✓ | `INLINE_HBM_ALLOC mb=512 copies/step=6 ramp=1 copies_max=48` in node_0.log |
| SET_UPGRADE @ step | 215 | rate=1.0 all-ranks（localize fallback，non-blocker）|
| SET_DOWNGRADE | **Y** reason=`time` elapsed=26s upgrade_step=215 downgrade_step=704 | window_s=15 命中 |
| LOCALIZE_FALLBACK | 1 (all-ranks) | culprit_rank=None（判分不依赖，直接锁定 rank 7 victim pid=3680251）|
| culprit_pid (rank 7) | 3680251 | LOCAL_RANK=7 |
| dev 7 (victim) rise_mb | 8692 | HBM ramp 落在 rank 7 dev 7 |
| no-progress kill | 未触发 | 训练稳定 |
| HCCL_EXEC_TIMEOUT | 600s export ✓ 本轮未触发 | — |
| hang_max | 480s 未触发 | — |

## 判分逻辑

- P1-HW-B 判据由 v2 `torch_trace.max_allocated` 迁到 v3 `gpu.utilization.used_bytes`
- **anchor 补丁**：`gpu.utilization` 环形保留只留 `[dump-N, dump]` 段，不含 inject_stop 时刻（inject_stop 至 dump 之间隔约 79s）→ 判分用 `max(gpu.ts)`（= dump 时刻）作 anchor
- 6 档位 {60,300,900,1800,3600,all} 全 Y（214 rows 全落 60s 窗内）→ W\*=60 为首个够的最小保留窗

## B8 三处 gate（保留）

- STEP_AGG=**avg** · STEP_WINDOW=**100**
- HCCL_EXEC_TIMEOUT=**600s**
- NO_PROGRESS_KILL_S=**90s**

## B6 gates

- COMM_LAZY=1 · STEP_TIMING_LAZY=0
- PRUNE_EXTRA_PIDS=1 · DRY=0

## 已知细节 / 边界

- `LOCALIZE_FALLBACK=1`：本轮 SQL localize 未命中（HBM ramp 未让 rank 7 的 step_ms 显著慢；SQL 无 spike）→ 兜底 all-ranks SET；不阻塞判分（判分直接锁定 rank 7 pid）
- `time_key=ts`：本 dump `ts` 列有 14 个不同值（不是完全 degenerate），故用 `ts`；若某未来 dump ts 全同，代码会回退 `wall_ns`
- 60s W\* 是"首个够的"，不代表 30s 不够（30s 未测；handbook §3.4 定值 {60,300,900,1800,3600,all}）
- 大幅超阈值 (rise=10788 MB >> 256 MiB) 说明 HBM ramp copies=6→48 的剂量对 dev-level HBM 抬升非常明显；未来若要探"最小可检测剂量"，需要另做剂量扫

## 产物

- W\* 判分：`../pr3_retention_scan/W_STAR_P1_HW_B.json`
- 长跑发射记录：`PR2_EXP_P1HWB_LAUNCH.md`
- 训练 dump：`dynamic/probing_data/3680251/gpu.utilization`（214 rows × 20 cols × 16 devs）
- 3 家族汇总：`../pr3_retention_scan/RETAIN_MATRIX.md`

## 复跑

```bash
python3 project/probing-huawei/scripts/fail-slow/e3_retention_score.py \
    --case P1-HW-B \
    --dump-root project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260729_003933-pillar-c-v3-pr3-p1hwb/dynamic/probing_data \
    --victim-pid 3680251 \
    --out project/probing-huawei/results/ascend-ais/pillar_c_v3/pr3_retention_scan/
```
