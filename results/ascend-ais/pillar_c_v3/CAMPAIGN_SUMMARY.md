# Pillar C v3 · 战役摘要（2026-07-28 → 2026-07-29）

> **上一轮 v2 收官**：`pillar_c_v2/CAMPAIGN_SUMMARY.md`（72.6% 头条 + 5 处机制教训 + 3 处 UNRESOLVED）  
> **手册**：`project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md`  
> **产物根**：`results/ascend-ais/pillar_c_v3/`（PR-1 baseline + PR-2 localize + **PR-3 retention DONE** · **PR-4 DEFER**）  
> **本战役目标**：把 v2 UNRESOLVED 的机制问题（[READOUT-C]）落到 PR 级 diff，让头条数字获得"编排层 SQL 选出 culprit"的语义翻转 + 追溯窗 W\* 拿到三家族数字。  
> **最终收官**：见 `CAMPAIGN_FINAL.md`（PR-1/2/3 全绿 + PR-4 DEFER · 论文 outline §5.2.C 头条数字齐备）。

---

## 头条（可写论文）

| 主张 | v2 数字 | **v3 数字** | 语义变化 | 锚点 |
|------|--------:|------------:|----------|------|
| 同覆盖下数据量小 | 72.6% W\* | **88.28% W\*** (B8) / **92.20% W\*** (C) | v2=抢首个 worker pid；**v3=编排层 SQL 定位命中 GT rank 7** | B8 `20260728_204936` / C `20260728_211312` |
| 编排层 SQL 定位 culprit | — | **culprit_rank=7 GT** 稳定命中（A6+B8+C 三次一致） | v2 没有；v3 新增（手册 §2.2） | A6 `20260728_102830` |
| 设计追溯窗 W\* | 100（离线 E1-off） | **P1-SW-C=200 步 · P3-SW-A=60 秒 · P1-HW-B=60 秒**（PR-3 三家族齐） | v2 只有 E1-off 单点；v3 三家族每种关键数据各拿一个 W\* | PR-3 `pr3_retention_scan/RETAIN_MATRIX.md` |
| SET 键名统一 | 未落地 | **`probing.torch.profiling=` 唯一，旧键 alias+warn** | v2 P1-SW-C UNRESOLVED 根因 → v3 修复 | `torch.rs` |
| Retention 语义（PR-3） | MB 反推 | **`retain_steps` / `retain_secs` 显式 + 违规计数 + env/py SET 覆盖** | v2 只能靠环容量反推；v3 写入端观测 | PR-3 `pr3_retention_scan/PR3_SUMMARY.md` |
| 分级容量 + rate=0 稀采（PR-1） | v2 CPU 8MB 已改；GPU 32KB | **GPU util 8MB、GPU hccs 4MB；rate=0 稀采锚点 (`MIN_STEP_INTERVAL=500`)** | 时间线①"常驻期"证据地基通了 | PR-1 baseline `20260727_210243` |

---

## 时间轴

**总时长**：2026-07-28 15:00 → 2026-07-29 01:30（约 **10h30min wall clock**，**16 轮 loop**，**7 个 sub-agent**）

| 时段 | 主要活动 |
|------|----------|
| 前一日晚 – 07-28 早 | PR-1 baseline 长跑收官（`20260727_210243`），5 PASS + 1 估 PARTIAL；下发 PR-2 |
| 08:00 – 10:30 | 实验 A 系列（A1→A6）· 定位 SQL 每轮修一处（mode/pid 匹配/pytest）；A6 PASS `culprit_rank=7` |
| 10:30 – 14:30 | 实验 B 系列 (B→B5d) · dense_ranks / hang / rate=0 / hot-update / jsync 依次修 |
| 14:30 – 17:00 | B6 code 落地（lazy@table + prune_extra_pids）· Python-only 部署 + smoke |
| 17:00 – 20:00 | 实验 B7 长跑 · 头条 47.67% raw 但 mis-localize rank 5 + crash@146 |
| 20:00 – 20:40 | B7 crash 诊断 + B8 code（AVG SQL + no-progress kill + HCCL 600s）+ smoke |
| 20:40 – 21:10 | **B8 长跑 done 1042 步 · 88.28% W\* · culprit=7 GT** |
| 21:10 – 21:30 | **实验 C 追溯窗复现 · P1-SW-C · W\*=200 · duration_spike step=161 dur=0.53 module=DDP** |
| 21:30 – 22:53 | 收尾 · PR2_SUMMARY + CAMPAIGN_SUMMARY v3 · 下发 PR-3 |
| **22:53 – 23:16** | **PR-3 阶段 1**：Rust retention 语义落地（`ring_config.rs`/`exttbls.rs`）· wheel 编成（sha `9416803e…`）· 摆渡 grj bundle · pod 冒烟 4 项全绿 |
| **23:16 – 00:53** | **PR-3 阶段 2 前两 case**：P1-SW-C 复用实验 C dump 扫窗 → **W\*=200 步**；P3-SW-A 复用 B8 dump 扫窗 → **W\*=60 秒**（PR-1 8MB cpu.util 分级容量的直接收益，v2 UNRESOLVED 拿到数字）；P1-HW-B 无 dump → 补跑 |
| **00:53 – 01:00** | **PR-3 阶段 2 P1-HW-B 补跑收官**：`20260729_003933-pr3-p1hwb` 长跑 1000 步 done；judge anchor 迁 `max(gpu.ts)`；LOCALIZE_FALLBACK=1 但判分直接锁 rank 7 pid 3680251；**W\*=60 秒** rise=8692 MB @ dev 7、all-dev peak=10788 MB @ dev 12 |
| **01:00 – 01:30** | **PR-3 收官 + PR-4 阶段 0 摸底 DEFER**：`CAMPAIGN_SUMMARY.md` PR-3 章节写入；PR-4 摸底 —— pod IDLE 6 个（96 卡可用，机时充裕），但 **federation 未开通**（`probing list` 空 / `probing cluster nodes` Connection refused / 只 sshd），launch 大改（多 pod 协同 + sidecar 改造 + Rust `WITH FILTER` 下推）总工时估 **15-19h** 超 stretch 边界；handbook §4 明文允许"多机机时不够 PR-4 跳过"→ 写 `pr4_multinode/PR4_FEASIBILITY.md`（94 行），主 Loop 派 CAMPAIGN_FINAL 收工 |

---

## 流水线状态

| PR | 时间线段 | 状态 | 关键 run | 判定 |
|----|----------|:----:|----------|:----:|
| **PR-1** | ①常驻期 | ✅ **PARTIAL**（5 PASS + 1 估 PARTIAL） | `20260727_210243-yjr-as-b-pr1-health` | PASS |
| **PR-2** | ②→③→④触发/定位/SET | ✅ **PASS**（三实验完成；B 记 PARTIAL 为评分口径） | A6+B8+C（见头条） | **PASS** |
| **PR-3** | ⑥回查期 · retention 语义 | ✅ **DONE / PASS**（阶段 1 code+wheel+冒烟 4 项；阶段 2 三家族 W\* 全出） | code `PR3_CODE_STATUS.md`；exp `PR3_EXP_STATUS.md` / P1-HW-B `20260729_003933` | **PASS** |
| **PR-4** | 扩到多机 | 🟨 **DEFER · 见 PR4_FEASIBILITY** | — | Stretch defer |
| **附录 A** | 离线消融 | 🔲 DONE_PARTIAL（PR-2/3 期间未启动） | — | 可与 PR-4 并行 |

---

## Code diff 汇总

### PR-1（时间线①）
- **`ring_config.rs`（新增）**：真相源 `per_table_default_mb` / `table_ring_capacity_bytes` / `table_mmap_chunk_layout`；`PROBING_EXTTBL_<TABLE>_MB` 覆盖
- **`exttbls.rs`**：`PyExternalTableConfig::for_table`；单元测 `for_table_sets_tiered_defaults`
- **`cpu/collector.rs`, `gpu/collector.rs`, `gpu/hccs_collector.rs`**：`cpu.utilization` / `gpu.utilization` → 8MiB；`gpu.hccs` → 4MiB
- **`torch_probe.py`**：rate=0 稀采锚点（`PROBING_TORCH_MIN_STEP_INTERVAL=500`）；`Variables` 懒创建表（默认不写 `python.variables`）
- **`backends.py`**：`PROBING_SPAN_BACKENDS` 默认 `none`（关掉 `python.trace_event`）

### PR-2（时间线②③④）
- **`pillar_c_localize_culprit.py`（核心）**：`build_sql` + `STEP_MS_AGG_EXPR`（`avg`/`max`/`p95`）；`worker_pids_by_rank` 走 shm `python.torch_trace` 评分 + 每 rank 一 pid；`PILLAR_C_LOCALIZE_STEP_AGG=avg` 默认、`PILLAR_C_LOCALIZE_STEP_WINDOW=100` 默认（B8 修 B7 max+20 mis）
- **`torch.rs`**：SET 键名统一 `probing.torch.profiling=`，旧 `torch.profiling=` alias+warn
- **`core/table.py`**：`@table(lazy=True)` 让 `init_table()` 延迟到首次 `save()`（B6 lazy@table）
- **`torch_probe.py` / `collective/record.py`**：`TorchTrace/TorchStepTiming/CommCollective` 改 lazy；`PROBING_TORCH_COMM_COLLECTIVE_LAZY=1` 默认（rate=0 短路，仍 `note_last_comm` 保 cursor）
- **`prune_extra_pids.py`（新增）+ `hold_exec_run_case.sh`**：dump 前按 `worker_pids.txt` + `CULPRIT_PIDS` + 表签名剪 extra_pid；`PILLAR_C_PRUNE_EXTRA_PIDS=1` 默认开
- **`hold_exec_run_case.sh`（B8 driver）**：`export HCCL_EXEC_TIMEOUT=600`（默认 1800→600）；driver poll loop 加 `PILLAR_C_NO_PROGRESS_KILL_S=90` no-jsonl-progress kill
- **测试**：`test_pillar_c_localize_culprit.py` 13 passed；`test_pillar_c_set_window.sh` 时基优先 + 禁 skip 自检

### PR-3（时间线⑥）
- **`ring_config.rs`**：新增 `per_table_default_retain_{steps,secs}` / `table_retain_{steps,secs}` / `table_retention` + `TableRetention`；env `PROBING_EXTTBL_<T>_RETAIN_{STEPS,SECS}` 覆盖；`config_key()` 记 `probing.exttbl.<t>.<suffix>`；默认 `python.torch_trace`/`.comm_collective` retain_steps=500，`cpu.utilization`/`gpu.utilization` retain_secs=3600
- **`exttbls.rs`**：`PyExternalTableConfig.retain_{steps,secs}`；`ExternBacking` 加 per-chunk `min_step`/`min_ts` 与 `retention_violations_{step,secs}` 计数；`append()` advance 时对被回收 chunk 比对 `current - retain_*`，越界 `log::warn!` + 计数；py 方法 `retention()` / `set_retention()`
- **`pr3_retention_smoke.py`（新增）**：4 项冒烟（import 字段 / retain_steps 违规计数 / 运行时 set_retention / env 覆盖）
- **`e3_retention_score.py`（新增）**：3 case 通用判分（`judge_p3_sw_a_rss_time` + `judge_p1_hw_b_gpu`；6 档扫窗；不动 v2 `e1_offline_window_score.py`）；**本地 patch 未 commit**：judge anchor 迁 `max(gpu.ts)` / time_key 自适应 / victim-pid 直选
- **`_prep/launch_exp_p1hwb.sh`（新增）**：P1-HW-B 长跑发射（复用 B8 gates + INLINE HBM ramp 1b/512/6→48）；**本地遗留**：`shopt -s globstar` 未加 → `**` 单层匹配 → 判分手跑
- **cargo test**：`ring_config::` 10/10 PASS（含 5 项新 PR-3 用例）；`for_table_tests::` FAIL-BENIGN（pyo3 test-binary 缺 CPython 符号，pod 冒烟间接覆盖）

---

## Key numbers

| 项 | v2 | **v3** | 增减 |
|------|----:|----:|:---:|
| E3 dump 总量 | 2273 MiB (B5d 等)  | **815 MiB** (B7) → **1785 MiB** (B8 · dense=16) | B7 −64%；B8 相对 B7 涨是 dense_ranks 生效 |
| headline (W\*=100) | 72.6% | **88.28%** (B8) / **92.20%** (C) | <100% ✓；语义翻转 ✓ |
| dense_ranks | 1 (碰运气) | **7 是 GT 且 SET 到 GT**（B8 dense=16 是采样架构问题） | 语义翻转 |
| W\* P1-SW-C | 100（离线）| **200 步**（正式 C，anchor=282，spike @ step 161） | handbook §2.4 容忍窗内 |
| W\* P3-SW-A | UNRESOLVED | **60 秒**（B8 dump，PR-1 8 MiB cpu.util 分级容量收益） | 首次拿到数字 |
| W\* P1-HW-B | NO_W_STAR | **60 秒**（PR-3 长跑，判据迁 gpu.util used_bytes，rise 8692 MB @ dev 7） | 首次拿到数字 |
| P1-SW-C 状态 | UNRESOLVED (键错) | **PASS** (C `20260728_211312`) | ✅ |
| pytest（localize） | — | **13/13 PASS** | ✅ |
| Rust cargo test (memtable `ring_config::`) | — | **10/10 PASS**（含 5 项新 PR-3 用例） | ✅ |
| 训练完成率（长跑） | v2 全 done | B7 crash@146 → **B8/C/PR-3-P1HWB done** | ✅ |

---

## 对手对照（v2 UNRESOLVED vs v3 修复）

| v2 case | v2 状态 | 根因 | v3 修复 | v3 case 级验证 |
|---------|:-------:|------|-----------|:--------------:|
| `20260726_173830-pillar-c-e1-p1-sw-c-loud` | ❌ NO\_W\_STAR | SET 键 `torch.profiling=` 拼错 | 键名统一 `probing.torch.profiling=`；`torch.rs` alias+warn | ✅ **PASS · W\*=200 步**（PR-2 C `20260728_211312` + PR-3 判分）|
| E3 `20260726_181423` dense=1 | ✅ 但抢首个 pid | `hold_exec_run_case.sh` 首个 ATTACH_OK 就 `break` | **编排层 SQL 定位** + `PILLAR_C_SET_SCOPE=localize` 只对 culprit SET | ✅ **culprit_rank=7 GT 稳定**（A6+B8） |
| （v2 E3 数据量比 72.6%） | ✅ 数字 | 未有语义 | headline 从 72.6% 变 88.28%，但**是 GT 选出来的 rank** | ✅ 语义翻转（B8） |
| P3-SW-A 追溯窗 | ❌ UNRESOLVED | RSS 环 32KB 只留末尾 ~1s | **PR-1 8 MiB cpu.util 分级容量** + PR-3 判据 `judge_p3_sw_a_rss_time` | ✅ **W\*=60 秒**（B8 dump 扫窗，rise 443 MiB @ 60s）|
| P1-HW-B 追溯窗 | ❌ NO\_W\_STAR | 判据 `torch_trace.max_allocated` 平坦 | **判据迁 `gpu.utilization.used_bytes`** + PR-3 阶段 1 wheel 默认 `retain_secs=3600` | ✅ **W\*=60 秒**（`20260729_003933` 长跑，rise 8692 MB @ dev 7）|

**SET 键名统一 + 分级容量 + 判据迁 gpu.util 是 v2→v3 三大关键修复**：v2 里三家族里 2 家族 UNRESOLVED/NO_W_STAR，v3 全部拿到 W\* 数字。

---

## PR-1 / PR-2 / PR-3 依赖链

- **PR-1 → PR-3**：PR-1 分级容量把 `cpu.utilization` / `gpu.utilization` 环容量从 32 KB 升到 8 MiB。没有这份地基，P3-SW-A RSS 序列 v2 只留 1s、P1-HW-B gpu.util 也是空的。PR-3 才能在 dump 里扫 W
- **PR-2 → PR-3**：PR-2 SET 键名统一（`probing.torch.profiling=`）修好 P1-SW-C UNRESOLVED，让 PR-2 实验 C 的 dump 里 rank 7 有 rate=1.0 的完整 torch_trace 密采（含 spike @ step 161）。PR-3 P1-SW-C 直接复用这份 dump 扫窗，不必再跑
- **PR-3 → 后续**：`comm_collective` 无原生 step 列（若真要按 step 保留 comm 数据，需编排层补列）；SET 分发挂 engine registry；judge anchor 逻辑抽独立函数并加单测；`shopt -s globstar` 加到 launch 脚本头部；`dense_ranks=16` 与判据 =1 冲突（继承 PR-2）

---

## 审稿承诺（能诚实写 vs 不能写）

### 能诚实写
- **"编排层通过 SQL 定位 culprit（判据查询期现场写），仅对 culprit 升详"**（手册 §2.4 通过标志）
- **"审稿人问『你怎么选 culprit』→ 答『就是这段 SQL，写在编排里，想换判据改这条 SQL 就行』"**
- **"同覆盖归因数据量比 v3 = 88.28%"**（W\* content est，B8）
- **"追溯窗按关键数据分别是：P1-SW-C 200 步 / P3-SW-A 60 秒 / P1-HW-B 60 秒"**（PR-3 手册 §3.4 通过标志，配 evidence `duration_spike step=161 dur=0.529 module=DDP` / `rss rise 443 MiB span 59.2s` / `used_bytes rise 8692 MB @ dev 7`）
- **"关键小表 GPU util 8MB / hccs 4MB / cpu.util 8MB 分级容量"** + **"rate=0 稀采 `PROBING_TORCH_MIN_STEP_INTERVAL=500`"**（PR-1）
- **"retention 语义从 MB 反推变成显式 `retain_steps` / `retain_secs`，写入端观测+违规计数"**（PR-3 阶段 1）

### 不能写（诚实交底）
- ❌ "非 culprit rank 完全不写 torch_trace"（dense_ranks=16 说明 rate=0 也留稀采行；采样架构问题，PR-3 阶段 1 wheel 未修）
- ❌ "headline < v2 72.6%"（B8 88.28% > 72.6%；因每 rank 都写 20MB 环 + comm_collective 320 MB 未 lazy 生效）
- ❌ "W\*=100 正式复现"（C W\*=200；差异在 anchor_step 选取策略，handbook §2.4 容忍窗内但不是最紧）
- ❌ "no-progress kill 已在负向场景验证"（B7 之前 crash 后就修好了；B8/C/PR-3-P1HWB 都是正向没触发；负向留下次 hang case）
- ❌ "retention 硬拒推"（MEMT 单写入者语义，环满仍会 advance；违规只是 log::warn! + 计数）
- ❌ "PR-3 judge anchor 用 inject_stop_ts"（P1-HW-B 环形保留只留末尾一段，本地 patch 改用 `max(gpu.ts)`；未 commit）

---

## 交接给下轮

### PR-3 交付物（已 DONE · 本战役 22:53 – 01:00 收尾）
- `pr3_retention_scan/PR3_SUMMARY.md`（一页摘要，含 3 家族 W\* + 依赖链 + 3 处本地 patch 说明）
- `pr3_retention_scan/PR3_CODE_STATUS.md`（阶段 1 代码 + wheel + 冒烟 4 项）
- `pr3_retention_scan/PR3_EXP_STATUS.md` + `PR3_EXP_P1HWB_STATUS.md`（阶段 2 三家族 W\*）
- `pr3_retention_scan/{RETAIN_MATRIX,W_STAR_P1_SW_C,W_STAR_P3_SW_A,W_STAR_P1_HW_B}.{md,json}`
- **PR-3 遗留（下一 PR 优先做）**：
  - 3 处本地 patch commit 回 `probing-huawei` 主干（judge anchor 迁 `max(gpu.ts)` / LOCALIZE all-ranks 备用 / `shopt -s globstar`）
  - SET 分发挂 engine registry（当前 `ExternalTable.set_retention()` py 方法等效）
  - `comm_collective` 若要按 step 保留 → 编排层补 step 列
  - `dense_ranks=16` 与判据 =1 冲突（新增 `PROBING_TORCH_TRACE_LAZY_OTHER=1`，或改评分脚本 `dense_ranks` 定义）
  - `comm_collective` 320 MB 未 lazy 复查（继承 PR-2 遗留 #2）

### Stretch：PR-4 多机（手册 §4）—— **DEFER**

**2026-07-29 01:30 摸底结论**：**DEFER**（见 `pr4_multinode/PR4_FEASIBILITY.md` · 94 行）。

- **Pod IDLE 不是瓶颈**：yysong-master-0 / worker-0/1/2 + grj-megatron-32card-0716 master-0/worker-0 全部 IDLE，最大 6 pod × 16 卡 = **96 卡可用**
- **Federation 未开通**（阻塞点）：`probing list` 空、`probing cluster nodes` → Connection refused、`netstat` 只 sshd 无 probing HTTP／Unix socket、`pgrep -af probing` 无。**现有 pod 不常驻 probing daemon**（探针只是训练进程内注入库，训练结束即消失）；Rust 联邦 catalog / fanout / pushdown 代码就绪，缺**上层脚本让 daemon 常驻并让 pod 互相发现**
- **总工时估计**（超 stretch 30 min–2 h 边界）：
  - 4.1.a 编排层 `global.*` 定位 SQL：多 pod 协同 launch（**1.5-3 h**）+ sidecar 咬合改造（**1 h**）+ SQL 表名 `python.*` → `global.*`（**30 min**）+ debug（**≥4 h**）= **3-5 h**（可勉强今日）
  - 4.1.b 联邦源头过滤：`aggregate_pushdown.rs` 加 `WITH FILTER` 识别 + `federated_scan_exec.rs` 先发判据 SQL + Rust wheel 编+分发（**4-8 h**）+ 8-16 rank 实测 debug（**≥4 h**）= **10-14 h**（跨天）
  - **合计 15-19 h**
- **handbook §4 明文允许**（原文 L638）：*"若多机机时不够，PR-4 跳过；单机 PR-1/2/3 已够撑 outline §5.2.C 头条；PR-4 是 §5.3 万卡 case study 的前置。"* 现况机时不缺，但**工程复杂度不匹配 stretch 定位**——PR-1/2/3 已 PASS 且头条数字齐备，收 CAMPAIGN 更保论文进度

**PR-4 恢复的前置**（给下一战役 / outline §5.3 万卡 case study 独立立项）：
1. **daemon 常驻脚本**（新脚本 `probing_daemon_launch.sh`）：跳板 fanout N 个 pod 起 `torchrun --nnodes=N`，等 `probing cluster nodes` 探活成功；这是让 federation "开门"的先决条件
2. **`pillar_c_localize_culprit.py` 走 `global.*`**（pure Python，无 wheel 依赖，~30 min）：SQL 表名 `python.comm_collective` → `global.python.comm_collective`；先 `probing cluster nodes` 探活
3. **Rust `WITH FILTER` 下推**（联邦源头过滤 4.1.b，独立 PR）：`aggregate_pushdown.rs` + `federated_scan_exec.rs`；本机 rustup + cargo + maturin 编 abi3 wheel + 分发；预计 10-14 h
4. **多节点作业配置**：32 rank / 4 节点 · 沐曦 h3c 或华为 vc-a3-241ceshi 借身份；参考本轮 grj-megatron-32card-0716 IDLE 组合
5. **总工时预估 15-19 h**（daemon 常驻 3-5 h + 4.1.a 3-5 h + 4.1.b 10-14 h，含 debug）

### 已 DONE_PARTIAL 待续：附录 A 离线消融（手册 §5）
- 不占集群；可与 PR-4 并行；上次战役未启动

---

## 战役期间的机制教训（新增到 v2 6 条之上）

7. **P3-SW-A `inline_8a` GC 让全 rank 同步 wait**：定位 SQL 用 `MAX(step_duration_sec)` 会挑瞬时最慢 rank；改 `AVG` + window=100 才能反映持续被 sidecar 挂住的 GT rank
8. **HCCL 默认 30min timeout 太长**：不 export `HCCL_EXEC_TIMEOUT=600`，训练 stall 后要 30min 才收拾
9. **driver 死等无 poll**：训练 stall 后 torchrun 主进程无 poll，需要 driver 端加 no-jsonl-progress kill 90s 兜底
10. **pid 选择必须对齐 dump**：`--list-worker-pids` 用 ps 裸抓会撞 launcher/torchrun/utility pid；要用 shm `python.torch_trace` 评分 + 每 rank 一 pid（A6 修）
11. **`torch_trace` 全 rank 采样是 PROBING 默认**：`SET scope=localize` 只改 rank 7 pid 的 rate，非 culprit rank 的 dump 不减；评分脚本 `dense_ranks==1` 与实际架构对不上（PR-3 阶段 1 wheel 未修，遗留）
12. **B5c smoke 短测 vs B5b hang**：hot-update 后必须 `_sync_live_tracers` bundle 同步；否则 dump 前 tracer 是旧的
13. **PR-3 retention 判据 anchor 语义**：`gpu.utilization` 环形保留只留 `[dump-N, dump]` 段，inject_stop_ts 常在环外；judge anchor 应回退 `max(gpu.ts)`（本地 patch 未 commit）
14. **PR-3 SQL 定位对 HBM 渐衰抓不到**：`step_ms` 无 spike，SQL 命中 rank 5 而非 GT=7；judge 不依赖 localize，直接按 `LOCAL_RANK=7` 定位 rank 7 pid（LOCALIZE_FALLBACK=1 走 all-ranks，非阻塞）
15. **bash `**` 无 `shopt -s globstar` 不递归**：`launch_exp_p1hwb.sh` pull 后 score 步 `**/set_upgrade.log` 只匹配单层 → CULPRIT_PID 空 → 判分 SKIP；改法：`shopt -s globstar` 或改 `find … -name`

---

## 相关文档索引

| 文件 | 用途 |
|------|------|
| **`CAMPAIGN_FINAL.md`** | **战役最终收官**（一句话 + 论文 outline 影响 + 待办 for 后续 · 100-150 行）|
| **`pr3_retention_scan/PR3_SUMMARY.md`** | PR-3 一页摘要（本战役收官） |
| **`pr2_localize/PR2_SUMMARY.md`** | PR-2 一页摘要 |
| **`pr4_multinode/PR4_FEASIBILITY.md`** | PR-4 阶段 0 摸底 · **DEFER**（federation 未开 + 工时 15-19h 超 stretch）|
| `pr1_baseline/PR1_SUMMARY.md` | PR-1 前置 |
| `pr3_retention_scan/PR3_CODE_STATUS.md` | PR-3 阶段 1（代码 + wheel + 冒烟）|
| `pr3_retention_scan/PR3_EXP_STATUS.md` + `PR3_EXP_P1HWB_STATUS.md` | PR-3 阶段 2 三家族 W\* |
| `pr3_retention_scan/RETAIN_MATRIX.md` | 3 家族 W\* 汇总 + 分窗明细 |
| `pr2_localize/PR2_CODE_STATUS.md` | 代码 diff 全表（A→B8 每轮） |
| `pr2_localize/PR2_EXP_B8_STATUS.md` | 实验 B 长跑收官详报 |
| `pr2_localize/PR2_EXP_C_STATUS.md` | 实验 C 追溯窗复现详报 |
| `pr2_localize/PR2_LOCALIZE_ACC.md` + `_R{2..6}.md` | 实验 A 各轮验收 |
| `pr2_localize/PR2_TRACEWINDOW_P1SWC.md` | C 分窗表 |
| `pr2_localize/PR2_VOLUME.md` | headline 演进小表 |
| `project/reading-paper/writing/probing-paper/PILLAR-C-V3-EXECUTION-HANDBOOK.md` | 施工手册（§3.4 通过标志）|
| `project/reading-paper/writing/probing-paper/PILLAR-C-V2-DATA-READOUT-PLAIN.md` | v2 人话读数（触发 v3 起点） |
| `results/ascend-ais/pillar_c_v2/CAMPAIGN_SUMMARY.md` | v2 战役摘要（对手） |
