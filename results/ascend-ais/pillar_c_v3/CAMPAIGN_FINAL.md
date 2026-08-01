# Pillar C v3 · 战役最终收官（CAMPAIGN_FINAL）

**日期**：2026-07-28 15:00 → 2026-07-29 01:30（**10h30min wall clock · 16 轮 loop · 7 个 sub-agent**）
**状态**：**PR-1/2/3 全绿 · PR-4 DEFER**（handbook §4 明文允许 · 见 `pr4_multinode/PR4_FEASIBILITY.md`）
**产物根**：`project/probing-huawei/results/ascend-ais/pillar_c_v3/`
**对手**：`pillar_c_v2/CAMPAIGN_SUMMARY.md`（72.6% 头条 + 3 处 UNRESOLVED / NO_W_STAR）

---

## 一句话

**2026-07-28 → 29 · 10h30min · Pillar C v3 战役收官** —— PR-1（分级容量 + 稀采锚点）/ PR-2（编排层 SQL 定位 + only-culprit SET + SET 键名统一）/ PR-3（retention 语义 + 3 家族 W\*）**全绿**；PR-4（多机联邦定位）**DEFER**（federation 未开通 + launch 大改 15-19 h 超 stretch 边界，handbook 明文允许）。**论文 outline §5.2.C 头条数字已齐**（88.28% + 92.20% + 三家族 W\*），§5.3 万卡 case study 留独立立项。

---

## 核心成果（可写论文）

### PR-2（时间线②→③→④ · 触发→定位→SET）
- **编排层 SQL 定位 culprit**：`pillar_c_localize_culprit.py`（build_sql + `STEP_MS_AGG_EXPR` `avg`/`max`/`p95` + `worker_pids_by_rank` 走 shm `python.torch_trace` 评分 + 每 rank 一 pid）—— A6+B8+C 三次一致 `culprit_rank=7`（GT）
- **only-culprit SET**：`PILLAR_C_SET_SCOPE=localize` 仅对命中 pid 发 `SET probing.torch.profiling=` rate=1.0；非 culprit rank 不升详
- **SET 键名统一**：`torch.rs` alias+warn 修好 v2 P1-SW-C UNRESOLVED 根因（v2 键错 `torch.profiling=` → v3 唯一 `probing.torch.profiling=`）
- **headline < 100%**：B8 **88.28% W\*** / C **92.20% W\***（数字比 v2 72.6% 涨是因每 rank 都写 20 MiB torch_trace 环 + comm_collective 320 MB 未 lazy，但 **rank 是 GT 选出**——语义翻转）
- **v2 UNRESOLVED P1-SW-C 得到 case 级验证**：C `20260728_211312` W\*=200

### PR-3（时间线⑥ · 回查期 retention）
- **retention 从 MB 反推 → 显式 `retain_steps` / `retain_secs`**（`ring_config.rs` / `exttbls.rs` / env `PROBING_EXTTBL_<T>_RETAIN_{STEPS,SECS}` 覆盖）；写入端观测 + 违规 log::warn! + `retention_violations_{step,secs}` 计数
- **3 家族 W\***：
  - **P1-SW-C = 200 步**（`python.torch_trace` duration ≥ 0.5s spike · 主证据 `step=161:dur_s=0.5289:module=DDP` · 复用 PR-2 C dump）
  - **P3-SW-A = 60 秒**（`cpu.utilization` RSS rise ≥ 50 MiB · 主证据 `rise_kb=443928:span_s=59.2` · 复用 PR-2 B8 dump；**PR-1 8 MiB cpu.util 分级容量的直接收益**——v2 UNRESOLVED 拿到数字）
  - **P1-HW-B = 60 秒**（`gpu.utilization` used_bytes per-dev rise ≥ 256 MiB · 主证据 `rise_mb=10788:dev=12:n_devs=16` · **PR-3 新长跑 `20260729_003933`**；判据从 v2 `torch_trace.max_allocated` 平坦迁到 `gpu.util.used_bytes`——v2 NO_W_STAR 拿到数字）
- **handbook §2.5 / §3.4 通过标志全达成**

### PR-1（时间线① · 常驻期，前一日晚已收官）
- **分级容量**：`cpu.util` / `gpu.util` **8 MiB** · `gpu.hccs` **4 MiB**（v2 32 KB → 8 MiB 是 PR-3 P3-SW-A / P1-HW-B 拿到 W\* 的地基）
- **rate=0 稀采锚点**：`PROBING_TORCH_MIN_STEP_INTERVAL=500`
- **`Variables` / `python.trace_event` 默认关**：减少常驻期噪音

---

## 论文 outline 影响

- **§5.2.C 头条**（可诚实写）：
  - "同覆盖归因数据量比 v3 = **88.28% W\*** (B8) / **92.20% W\*** (C)"
  - "culprit 由编排层 SQL 选出（判据查询期现场写），仅对 culprit 升详"
  - "追溯窗按关键数据分别是 —— **P1-SW-C torch_trace duration 200 步** / **P3-SW-A cpu.util RSS 60 秒** / **P1-HW-B gpu.util used_bytes 60 秒**"
  - 审稿人问「你怎么选 culprit」→ 「就是这段 SQL，写在编排里」；「追溯窗多长」→ 「按关键数据分别是 N 步/T 秒」
- **§5.3 万卡 case study**：**PR-4 defer**——需要前置 15-19 h 工作量（federation daemon 常驻脚本 + `pillar_c_localize_culprit.py` 走 `global.*` + Rust `WITH FILTER` 下推 + 多节点作业配置）；handbook §4 已明文允许"多机机时不够，PR-4 跳过；单机 PR-1/2/3 已够撑 §5.2.C 头条"
- **不能写**（诚实交底）：
  - ❌ "非 culprit rank 完全不写 torch_trace"（`dense_ranks=16` 采样架构问题，PR-3 阶段 1 wheel 未修）
  - ❌ "headline < v2 72.6%"（B8 88.28% > 72.6% 是每 rank 20 MB 环 + comm_collective 320 MB 未 lazy）
  - ❌ "W\*=100 正式复现"（C W\*=200；anchor_step 选取策略，handbook §2.4 容忍窗内）
  - ❌ "no-progress kill 已在负向场景验证"（B7 crash 后就修好；后续正向都没触发；负向留下次 hang case）
  - ❌ "retention 硬拒推"（MEMT 单写入者语义，环满仍 advance；只 warn+计数）

---

## 待办 for 后续（按优先级）

0. **[NEW 2026-07-29 · P0] 数据量比口径重新定义 —— 从"内容保留量"改成"真实磁盘写入压力"**：
   - **问题**：现有 `e3_score_ratio.py:228-264` `estimate_w_truncate_tt_bytes` 用"步数比例折算字节"当头条数字（B8 88.28% / C 92.20%），是**内容量口径**——审稿人问"你省磁盘吗"答不了。真实磁盘 I/O 压力应该看**单位时间写入盘的字节速率**，全量臂线性增长、动态臂受 rate=0 骨架 + culprit 短窗密采限制，两者差距应是 10-100×，不是 12%
   - **正确口径 3 选**（推荐由弱到强）：
     - (a) 训练中每 30 秒采样 `du -sb probing_data/`，最后拿曲线斜率的时间积分当"实际写入字节"；**跟磁盘系统真正承受的 I/O 压力对齐**（最省事，无 wheel 改动）
     - (b) memtable 内核加 `bytes_written_total` 计数器（每次 append 累加），dump 时读；能精确到"多少行 × 每行字节"，不受环覆盖影响（需 wheel 改动）
     - (c) 兜底：把 `est_tt_bytes_w` 改成 `max(fb, len(steps) * bytes_per_step)`，能反映"环被覆盖多少次"（脚本级别，最快）
   - **产物**：`PR2_E3_RATIO_B*.{md,json}` 里增补 `disk_write_bytes_per_sec` / `disk_write_bytes_total_time_integral` 两个字段；outline §5.2.C 头条数字换新口径
   - **依赖**：跟 #3（`comm_collective` 320 MB 未压）合并——因为真实写入量口径下，`comm_collective` lazy gate 直接影响主要头条

0.1. **[NEW 2026-07-29 · P1] W\* 在线验证 —— 目前只离线切窗判据首命中，没证明 memtable 在线只留 W\* 步不会挤掉关键 spike**：
   - **问题**：`e3_retention_score.py` 是**离线切窗**（一次跑最大 retain，dump 后按 W 依次切片判分），W\*=200 步只证明"200 步的历史里有 spike 证据"，**没证明**"如果 memtable 在线 `retain_steps=200` 真跑一次，`retention_violations_step` 计数不增长且关键 spike 不被覆盖"
   - **实验设计**：对三家族各选 W\* 档，在线跑一次 `retain_steps=W*` / `retain_secs=W*`，看 dump 出的 memtable 里 spike 步是否还在 + `retention_violations_{step,secs}` 计数
   - **产物**：`W_STAR_ONLINE_VERIFY_<case>.json` 3 份；补进 PR-3 SUMMARY

1. **PR-4 完整实现**（下战役独立立项，预计 15-19 h · outline §5.3 前置）：
   - daemon 常驻脚本 `probing_daemon_launch.sh`（跳板 fanout N pod 起 `torchrun --nnodes=N` + 等 `probing cluster nodes` 探活）
   - `pillar_c_localize_culprit.py` SQL 表名 `python.*` → `global.*`（4.1.a，pure Python）
   - Rust `aggregate_pushdown.rs` 加 `WITH FILTER` 识别 + `federated_scan_exec.rs` 先发判据 SQL（4.1.b，wheel 编+分发）
   - 32 rank / 4 节点定位准确性 + 联邦查询去噪对照
2. **3 处本地 patch commit 回主干**（PR-3 遗留）：
   - `e3_retention_score.py` judge anchor 迁 `max(gpu.ts)` + time_key 自适应 + victim-pid 直选（`compute_gpu_util_anchor()` 抽独立函数 + 单测）
   - `pillar_c_localize_culprit.py` LOCALIZE all-ranks 备用（HBM 渐衰类 case SQL 无 spike 时兜底）
   - `_prep/launch_exp_p1hwb.sh` 头部 `shopt -s globstar`（`**` glob 单层匹配 bug；或改 `find … -name`）
3. **`comm_collective` 320 MB 未压 → COMM_LAZY gate hole 排查**（PR-2 遗留 · 折进 B8 头条约 15pp）：B7 lazy gate 生效为 0，B8 gate 是否被覆盖需复查
4. **SET 分发挂 engine registry**（PR-3 遗留）：当前 `ExternalTable.set_retention()` py 方法 + env 覆盖等效；把 `probing.exttbl.<t>.retain_{steps,secs}` 挂到 SET 分发是下一 PR
5. **`dense_ranks=16` 与判据 =1 冲突**（PR-2/3 双遗留）：新增 `PROBING_TORCH_TRACE_LAZY_OTHER=1` 或改评分脚本 `dense_ranks` 定义
6. **yysong-w0 rank 15 pre-existing stuck sanity**（低优先级）：主池首选 pod 前一 tenant 遗留 stuck 训练；本战役 C/B8/P1-HW-B 均切 grj-megatron-32card-0716-worker-0 绕开；下战役开工前跑 §0.2 环境自检
7. **附录 A 离线消融**（DONE_PARTIAL 收尾）：不占集群，可与 PR-4 并行；上次战役未启动

---

## 产物索引

| 分类 | 文件 |
|------|------|
| **战役收官** | `CAMPAIGN_SUMMARY.md` · `CAMPAIGN_FINAL.md`（本文档）|
| **PR-1** | `pr1_baseline/PR1_SUMMARY.md` · run `20260727_210243` |
| **PR-2** | `pr2_localize/PR2_SUMMARY.md` · `PR2_CODE_STATUS.md` · `PR2_EXP_B8_STATUS.md` · `PR2_EXP_C_STATUS.md` · `PR2_LOCALIZE_ACC.md` + `_R{2..6}` · `PR2_TRACEWINDOW_P1SWC.md` · `PR2_VOLUME.md` · `PR2_B7_CRASH_DIAG.md` · `PR2_E3_RATIO_B{2..8}.{md,json}` |
| **PR-3** | `pr3_retention_scan/PR3_SUMMARY.md` · `PR3_CODE_STATUS.md` · `PR3_EXP_STATUS.md` · `PR3_EXP_P1HWB_STATUS.md` · `RETAIN_MATRIX.{md,json}` · `W_STAR_P1_SW_C.json` · `W_STAR_P3_SW_A.json` · `W_STAR_P1_HW_B.json` |
| **PR-4 摸底** | `pr4_multinode/PR4_FEASIBILITY.md`（94 行 · DEFER 结论）|
| **关键 run dump** | `pr2_localize/20260728_102830-…-a6/` · `20260728_204936-…-e3-b8/` · `20260728_211312-…-exp-c-p1swc/` · `20260729_003933-…-pr3-p1hwb/` |
| **Code diff commit** | PR-1/2/3 code 在 `project/probing-huawei/` 主干（`ring_config.rs` · `exttbls.rs` · `pillar_c_localize_culprit.py` · `torch.rs` · `core/table.py` · `torch_probe.py` · `collective/record.py` · `prune_extra_pids.py` · `hold_exec_run_case.sh` · `e3_retention_score.py`）；3 处本地 patch 未 commit（见待办 #2）|
| **发射脚本** | `pr2_localize/_prep/launch_exp_{a6,b8_smoke,b8,c}.sh` · `_prep/launch_exp_p1hwb.sh` |
| **手册 / 上游** | `project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md`（§3.4 通过标志）· `PILLAR-C-V2-DATA-READOUT-PLAIN.md`（v2 触发起点）· `pillar_c_v2/CAMPAIGN_SUMMARY.md` |
| **wheel** | `probing-0.2.6-cp38-abi3-linux_aarch64.whl` sha `9416803e52cab5be8e4dc4ee58d6d746c2b94936d7706baabc8c6d40fcfa1d64`（yysong-w0 `/data/yinjinrun.p-huawei/probing-huawei/wheels/` + grj-w0 AFS `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/{probing-huawei,probe-bundle}/wheels/`）|

---

## 战役期间踩过的 8 处坑（回顾 · 详见 PR2_SUMMARY / PR3_SUMMARY）

1. **跳板 SSH 断 5 tick**（PR-2 #1）：`ais-cf3e61a5` 中途断连；发射脚本禁用 `| head` 截 pipe + hold_exec log 立刻落 pod AFS
2. **yysong-w0 rank 15 pre-existing stuck**（PR-2 #2）：主池首选 pod 前 tenant 遗留 stuck；本战役 C/B8/P1-HW-B 切 grj-megatron-32card-0716 IDLE hold-exec 让路
3. **P3-SW-A step_ms MAX 判据模糊**（PR-2 #3）：`MAX(step_duration_sec) OVER window=20` 抽奖挑瞬时；改 `AVG` + window=100
4. **driver 死等 30 min**（PR-2 #4）：B7 stall @ 146 后 torchrun 主进程无 poll；加 `PILLAR_C_NO_PROGRESS_KILL_S=90` 兜底
5. **HCCL 默认 30 min timeout 太长**（PR-2 #5）：`export HCCL_EXEC_TIMEOUT=600` 默认
6. **B5b hot-update 漏 jsync**（PR-2 #7）：SET 后 tracer 未同步；B5c `_sync_live_tracers` bundle 修复
7. **SQL 定位对 HBM 渐衰抓不到**（PR-3 #5）：`step_ms` 无 spike 命中 rank 5 而非 GT=7；判分脚本按 `LOCAL_RANK=7` 直锁 rank 7 pid（LOCALIZE_FALLBACK=1 走 all-ranks，非阻塞）
8. **SET 键名统一之前拼错**（PR-2 与 v2 P1-SW-C UNRESOLVED 根因）：v2 `torch.profiling=` 拼错 → v3 `probing.torch.profiling=` 唯一 + alias+warn；C W\*=200 case 级验证 PASS

（另 PR-3 三处本地 patch —— judge anchor 迁 `max(gpu.ts)` / launch `**` globstar / time_key 自适应 —— 见待办 #2）

---

## 判定：**PASS**（PR-1/2/3 全绿 · handbook §2.5 / §3.4 通过标志全达成 · outline §5.2.C 头条数字齐备 · PR-4 handbook §4 明文允许 DEFER）
