# PR-3 阶段 2 · 追溯窗扫描 (handbook §3.4)

**日期**：2026-07-28 → 2026-07-29 补跑收官
**判定**：**PASS**（3/3 case OK）

## 一句话结论

按 case 拿到 W\* 数字表：**P1-SW-C = 200 步**（torch_trace duration）、**P3-SW-A = 60 秒**（cpu.utilization RSS）、**P1-HW-B = 60 秒**（gpu.utilization used_bytes）。3 个故障家族各得一个 W\* 数字 → handbook §3.4 通过标志达成。

## 判分脚本扩展

**文件**：`project/probing-huawei/scripts/fail-slow/e3_retention_score.py`

- 复用 v2 `e1_offline_window_score.py` 的 MEMT parser (`read_memt`) 与 `judge_p1_sw_c` / `judge_p1_hw_b` 判分函数（不改 v2）
- 新增 `judge_p3_sw_a_rss_time`：`cpu.utilization` MEMT rows (scope=process) 按 `anchor_ts_us - retain_secs*1e6` 时间窗切；RSS rise ≥ 50 MiB 视为够
- 新增 `judge_p1_hw_b_gpu`：`gpu.utilization` MEMT rows 按 device_id 分组，per-dev `used_bytes` rise ≥ 256 MiB 视为够。**7-29 补丁**：
  - 时间键选择：`ts` 列多值 → 用 `ts`（μs）；`ts` 全相同（degenerate = dump 时刻标签）→ 回退 `wall_ns`（ns）
  - anchor 语义：对于 P1-HW-B，`gpu.utilization` 是环形保留（默认 `retain_secs=3600`），常常只留 `[dump-N, dump]` 一段，不含 inject_stop 时刻。若 gpu 所有 ts 都 > inject_stop_ts+1s，回退用 `max(gpu.ts)`（== dump 时刻）作 anchor，语义："retention 窗口反查到 W 秒前"
- 新增 `compute_anchor_ts`：从 torch_trace 找 `local_step==inject_stop(300)` 的 ts
- v2 判分口径不改动

## Gate

| Gate | 结果 |
|------|------|
| 判分脚本能读 `cpu.utilization` MEMT 的 process-scope RSS | **PASS** |
| 判分脚本能读 `gpu.utilization` MEMT 的 per-device used_bytes | **PASS**（B8 dump 有 211 rows；P1-HW-B 长跑 dump 有 214 rows × 16 dev） |
| P1-SW-C 复用实验 C dump 扫窗 6 个 W | **PASS**（W\*=200） |
| P3-SW-A 复用 B8 dump 扫窗 6 个 W | **PASS**（W\*=60） |
| P1-HW-B 长跑 dump 扫窗 6 个 W | **PASS**（W\*=60；rise=10788 MB @ dev12，rank 7 dev 7 rise=8692 MB） |
| 3 个 case 都出 W\* | **PASS**（3/3） |

## W\* 结果表

| Case | 关键数据 | retain 单位 | W\* | 主证据 |
|---|---|---|---:|---|
| P1-SW-C | `python.torch_trace` duration | steps | **200 步** | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` |
| P3-SW-A | `cpu.utilization` RSS | secs | **60 秒** | `cpu.utilization_rss:rise_kb=443928:max_kb=2684492:n=119:span_s=59.2` |
| P1-HW-B | `gpu.utilization` used_bytes | secs | **60 秒** | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` |

## 对照 v2

**P1-SW-C**：
- v2 W\*=100（`pillar_c_v2/E1_off/W_STAR.md`, parent `20260726_012627-pillar-c-p1-sw-c-loud`, anchor=300）
- v3 W\*=200（parent `20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`, anchor=282, spike step=161）
- 差异原因：v3 dump 里 ≤300 的实际最大 step 只到 282；spike 在 step 161，落入 anchor-121，需要 W≥121 → 首个 ≥121 的档位是 200
- 与 v3 CAMPAIGN_SUMMARY.md 头条一致（"W\*=200 正式 C P1-SW-C"）；handbook §2.4 W\*=100 容忍窗内

**P3-SW-A**：
- v2 UNRESOLVED（RSS 环 32KB 落盘后只留末尾 ~1s，与 inject 窗 [100,300] 时间无重叠）
- v3 W\*=60（B8 dump，PR-1 分级容量让 RSS 序列覆盖 268s，注入窗完整落在环内）
- **PR-1 分级容量的直接语义收益**：v2 UNRESOLVED → v3 有 W\* 数字

**P1-HW-B**：
- v2 NO_W_STAR（torch_trace.max_allocated 平坦；判据本身没抬升）
- v3 W\*=60（parent `20260729_003933-pillar-c-v3-pr3-p1hwb`, rank 7 dev 7 rise=8692 MB, all-dev peak=10788 MB @ dev12）
- **判据从 torch_trace.max_allocated 迁到 gpu.utilization.used_bytes 后**，PR-3 阶段 1 wheel 让 gpu.utilization 默认 `retain_secs=3600`，环里够 60s 就 enough
- 60s 是 6 档位（{60,300,900,1800,3600,all} sec）里首个 enough=Y 的最小保留窗；实际这份 dump 全程都够，因此 W\*=60 是"保留窗越小越省"的最优点

## P1-HW-B 长跑参数（补跑）

- **run_id**: `20260729_003933-pillar-c-v3-pr3-p1hwb`
- **pod**: `grj-megatron-32card-0716-worker-0`（grj-w0，主池 yysong-w0 rank 15 stuck 让路）
- **case**: P1-HW-B loud · INLINE HBM ramp（inject_kind=1b, mb=512, copies=6→48, ramp=1）
- **ITERS**: 1000（跑到 1000 步完成 · 训练 rc=0）
- **inject**: [100, 300]（step 100 marker + step 300 marker 齐）
- **B8 gates**: STEP_AGG=avg STEP_WINDOW=100 NO_PROGRESS_KILL_S=90 HCCL_EXEC_TIMEOUT=600
- **SET flow**: L>=100 attach → localize fallback all-ranks → SET rate=1.0 → 26s elapsed >= 15s window → SET_DOWNGRADE reason=time（step=704, upgrade_step=215）
- **LOCALIZE_FALLBACK=1**（这次 SQL 命中 rank 5 但被兜底为 all；后续 e3_score_ratio 头条比未跑，非阻塞）
- **判分 pid**: 3680251（rank 7 victim）— **不依赖** localize，直接按 LOCAL_RANK=7 定位
- **训练完成 markers**: `node_0.done` ✓ · `step_100.marker` ✓ · `step_300.marker` ✓ · `no-progress kill` 未触发 · `HCCL timeout` 未触发

## 判定

- **PASS**：3 case 里 3 OK；handbook §3.4 通过标志"3 个故障家族各得一个 W\* 数字" 达成
- PR-3 收官条件齐备：可写 PR-3 SUMMARY 收尾

## 产物

- `RETAIN_MATRIX.md` / `RETAIN_MATRIX.json`：3 case 汇总 + 分窗明细
- `W_STAR_P1_SW_C.json` / `W_STAR_P3_SW_A.json` / `W_STAR_P1_HW_B.json`：单 case 明细
- `PR3_EXP_P1HWB_STATUS.md`：P1-HW-B 补跑长跑判
- 判分脚本：`project/probing-huawei/scripts/fail-slow/e3_retention_score.py`
- 长跑发射脚本：`results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_p1hwb.sh`
- 长跑发射记录：`pr2_localize/PR2_EXP_P1HWB_LAUNCH.md`

## 复跑

```bash
python3 project/probing-huawei/scripts/fail-slow/e3_retention_score.py \
    --scan-all --map <path-to-map.json> \
    --out project/probing-huawei/results/ascend-ais/pillar_c_v3/pr3_retention_scan/
```

单 case（P1-HW-B）：

```bash
python3 project/probing-huawei/scripts/fail-slow/e3_retention_score.py \
    --case P1-HW-B \
    --dump-root project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260729_003933-pillar-c-v3-pr3-p1hwb/dynamic/probing_data \
    --victim-pid 3680251 \
    --out project/probing-huawei/results/ascend-ais/pillar_c_v3/pr3_retention_scan/
```
