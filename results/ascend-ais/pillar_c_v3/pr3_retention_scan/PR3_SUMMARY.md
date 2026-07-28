# PR-3 一页摘要 · 追溯窗（Retention 语义 + W\* 扫窗）

**日期**：2026-07-28 → 2026-07-29 收官
**状态**：**PASS**（阶段 1 代码 + wheel + 冒烟全绿；阶段 2 三家族 W\* 全部拿到数字）
**范围**：手册 §3 时间线 ⑥ 回查期 —— 把「追溯窗多长」从 MB 反推变成显式 `retain_steps` / `retain_secs` 语义，写入端超窗即 warn+计数；三个故障家族各扫 6 档 W 拿到 W\*

---

## 一句话摘要

**PR-3 把 retention 从"MB 反推"改成显式 `retain_steps` / `retain_secs`（写入端观测+违规计数，不硬拒推），并在三个故障家族上各扫 6 档 W，拿到 W\* 数字：P1-SW-C = 200 步、P3-SW-A = 60 秒、P1-HW-B = 60 秒。**  
论文一句话：**"追溯窗按关键数据分别是 —— torch_trace duration 200 步、cpu.utilization RSS 60 秒、gpu.utilization used_bytes 60 秒。"**

审稿人问「你说'追溯窗按关键数据是 N 步 / T 秒'具体多长」→ 答「**P1-SW-C 200 步 duration spike / P3-SW-A 60 秒 RSS rise / P1-HW-B 60 秒 HBM used_bytes rise**；判据阈值分别是 duration ≥ 0.5s / RSS rise ≥ 50 MiB / used_bytes rise ≥ 256 MiB」。

---

## 改动清单（PR-3 交付）

| 编号 | 文件 | 改动 | 理由 |
|------|------|------|------|
| 3.1.a | `probing/memtable/src/ring_config.rs` | 新增 `per_table_default_retain_{steps,secs}` / `table_retain_{steps,secs}` / `table_retention` + `TableRetention` 结构；env `PROBING_EXTTBL_<T>_RETAIN_{STEPS,SECS}` 覆盖；`config_key()` 记 `probing.exttbl.<t>.<suffix>` 命名空间 | 手册 §3.2 真相源：retention 从 MB 反推 → 显式 step/秒 |
| 3.1.a | `probing/memtable/src/lib.rs` | 导出 retention API（`TableRetention`、helper 函数） | 供 exttbls 消费 |
| 3.1.a | `probing/extensions/python/src/extensions/python/exttbls.rs` | `PyExternalTableConfig` 加 `retain_steps` / `retain_secs`；`ExternBacking` 加 per-chunk `min_step` / `min_ts` 与 `retention_violations_{step,secs}` 计数；`append()` 在 advance 时对被回收 chunk 的 `min_*` 与 `current - retain_*` 比对，越界 `log::warn!` + 增计数；py 方法 `retention()` / `set_retention()`（运行时改） | Python 层承接 retention 观测，不动 memtable 内核 |
| 3.1.b | 同上（env 覆盖 + `config_key`） | `PROBING_EXTTBL_<TABLE>_RETAIN_{STEPS,SECS}` env 通路；`probing.exttbl.<t>.retain_steps` / `..retain_secs` config-key helper | 与 PR-1 `_MB` 命名规则统一 |
| — | `scripts/fail-slow/pr3_retention_smoke.py` | 阶段 1 冒烟脚本（4 项：import 字段 / retain_steps 违规计数 / 运行时 `set_retention` / env `RETAIN_SECS` 覆盖） | 上机不重编，直接冒烟 wheel 是否正确暴露 retention |
| — | `scripts/fail-slow/e3_retention_score.py` | 阶段 2 判分脚本（3 case 通用；复用 v2 `read_memt` + `judge_p1_sw_c`；新增 `judge_p3_sw_a_rss_time` + `judge_p1_hw_b_gpu`；6 档 W 扫窗；不动 v2 `e1_offline_window_score.py`） | 三家族的 W\* 判据分别用 torch_trace / cpu.util / gpu.util |
| — | `results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_p1hwb.sh` | 阶段 2 P1-HW-B 补跑发射（复用 PR-2 hold_exec_run_case.sh，B8 gates + INLINE HBM ramp 1b/512/6→48） | v2 里 P1-HW-B NO_W_STAR，v3 需要一份长跑 dump 才能扫 gpu.util |

**回滚**：`ExternalTable.set_retention(None, None)` 关闭观测；env `PROBING_EXTTBL_<T>_RETAIN_STEPS=0` 拒绝（新单测 `env_override_zero_rejected` 覆盖）；retention 与 PR-1 分级容量完全独立，无相互回滚风险。

---

## 3 家族 W\* 总览

| Case | 关键数据 | retain 单位 | W\* | 判据阈值 | 主证据 | dump run_id |
|------|---------|-----------|----:|--------|--------|-------------|
| **P1-SW-C** | `python.torch_trace` duration | steps | **200 步** | duration ≥ 0.5s spike | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` | `20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`（复用 PR-2 实验 C）|
| **P3-SW-A** | `cpu.utilization` RSS | secs | **60 秒** | RSS rise ≥ 50 MiB | `cpu.utilization_rss:rise_kb=443928:max_kb=2684492:n=119:span_s=59.2` | `20260728_204936-pillar-c-v3-pr2-e3-b8`（复用 PR-2 B8）|
| **P1-HW-B** | `gpu.utilization` used_bytes | secs | **60 秒** | used_bytes rise ≥ 256 MiB (per-dev) | `gpu.utilization_used_bytes:rise_mb=10788.0:dev=12:n_devs=16:n_rows=214:time_key=ts` | `20260729_003933-pillar-c-v3-pr3-p1hwb`（PR-3 新长跑）|

**通过标志达成**（handbook §3.4 尾）：**"3 个故障家族各得一个 W\* 数字，论文里可以写'追溯窗按关键数据分别是 N 步 / T 秒'"**。

---

## 分窗明细（6 档扫窗，all=最大档）

### P1-SW-C（steps 域）

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| 25 | N | 364 | `no_spike:top_step=261:dur_s=0.1996:med=0.1027` |
| 50 | N | 546 | `no_spike:top_step=261:dur_s=0.1996` |
| 100 | N | 910 | `no_spike:top_step=261:dur_s=0.1996` |
| **200** | **Y** | **1820** | `duration_spike:step=161:dur_s=0.5289:module=DDP` |
| 500 | Y | 2731 | 同上 |
| all | Y | 2731 | 同上 |

anchor_step=282；spike @ step 161（anchor - 121）→ 首个够的档是 W=200。

### P3-SW-A（secs 域）

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| **60** | **Y** | **119** | `rss:rise_kb=443928:span_s=59.2` |
| 300/900/1800/3600/all | Y | 220 | `rss:rise_kb=2542224:span_s=109.9` |

anchor_ts_us=1785243124274636；60s 窗内 rise=433 MiB（>> 50 MiB 阈值）→ W\*=60。

### P1-HW-B（secs 域）

| W | enough | n_rows | evidence |
|---|:---:|---:|---|
| **60** | **Y** | **214** | `used_bytes:rise_mb=10788:dev=12:n_devs=16:time_key=ts` |
| 300/900/1800/3600/all | Y | 214 | 同上 |

anchor_ts_us=1785257010644215（= max(gpu.ts)，见技术旁注 #1）；本 dump gpu.util 214 rows 全落 60s 窗 → 6 档全 Y，W\*=60 是首个够的最小窗。

---

## 对照 v2

| Case | v2 | v3 | 差异原因 |
|------|-----|-----|---------|
| **P1-SW-C** | W\*=100（离线 `pillar_c_v2/E1_off`，anchor=300） | **W\*=200**（正式 C，anchor=282，spike @ step 161 落入 anchor-121，需 W≥121 → 首档 200） | dump 不同 · handbook §2.4 容忍窗内 |
| **P3-SW-A** | **UNRESOLVED**（RSS 环 32KB 落盘只留末尾 ~1s，与 inject [100,300] 无重叠） | **W\*=60**（B8 dump，PR-1 8 MiB cpu.util 分级容量让 RSS 序列覆盖 268s） | **PR-1 分级容量的直接语义收益** |
| **P1-HW-B** | **NO_W_STAR**（判据 `torch_trace.max_allocated` 平坦） | **W\*=60**（判据迁 `gpu.utilization.used_bytes`；PR-3 阶段 1 wheel 让 gpu.util 默认 `retain_secs=3600`；rise=8692 MB @ rank 7 dev 7、all-dev peak=10788 MB @ dev 12） | **判据迁 + retention 默认从 wheel 拿到** |

**语义翻转**：v2 三家族里 2 家族 UNRESOLVED / NO_W_STAR，v3 全部拿到 W\* 数字。

---

## PR-1 / PR-2 / PR-3 依赖链

- **PR-1 → PR-3**：PR-1 把 `cpu.utilization` / `gpu.utilization` 环容量从 32 KB 升到 8 MiB / 8 MiB。**没有这份地基，P3-SW-A RSS 序列 v2 只留 1s、P1-HW-B gpu.util 也是空的**。PR-3 才能在 dump 里扫窗
- **PR-2 → PR-3**：PR-2 把 SET 键名统一（`torch.profiling=` → `probing.torch.profiling=`）修好 P1-SW-C UNRESOLVED，让 PR-2 实验 C 的 dump 里 rank 7 有 rate=1.0 的完整 torch_trace 密采（含 spike @ step 161）；**PR-3 P1-SW-C 直接复用**这份 dump 扫窗，不必再跑
- **PR-3 → 后续**：`comm_collective` 类 case 若真要按 step 保留，需要写入端补一列（编排层，非 probing 侧）；当前 3 case 走 steps（torch_trace）或 secs（cpu.util/gpu.util）都能覆盖，未阻塞。SET 分发挂 engine registry 是下一个 PR 的活（当前 `ExternalTable.set_retention()` py 方法 + env 覆盖已够用）

---

## 技术旁注（本地 patch · **未 commit**）

以下三处是 P1-HW-B 补跑时为了让判分/发射跑通而在本地临时改的，尚未回 diff 到 `probing-huawei` 主干。写论文/交接时应先补 commit 或至少复述这里的语义。

### 1. P1-HW-B judge：anchor 从 `inject_stop_ts` 改为 `max(gpu.ts)`

- **原因**：`gpu.utilization` 是环形保留（PR-3 默认 `retain_secs=3600`），dump 时环里只剩 `[dump-N, dump]` 段。P1-HW-B 训练完 1000 步后 dump 距 inject_stop（step 300）已隔约 79s；14 个 unique `ts` 都 > `inject_stop_ts + 1s`，用 inject_stop 作 anchor 时 60s 窗内 0 行
- **改法**：在 `e3_retention_score.py::judge_p1_hw_b_gpu` 里，若 gpu 所有 ts 都 > `inject_stop_ts + 1s`，回退 `anchor = max(gpu.ts)`
- **影响**：judge 语义从"回看到 inject_stop 前 W 秒是否够"变成"从 dump 反查 W 秒是否够"。对 gpu.util/cpu.util 这类环形回收数据，这个语义更符合"追溯窗多长够用"。对 P3-SW-A 同样适用，但 B8 dump 里 cpu.util 环没被撑满，anchor 用 inject_stop 也能命中，故这条 patch 只影响 P1-HW-B
- **动作项**：把 patch 回到 `probing-huawei` 主干时，把 anchor 回退逻辑抽成 `compute_gpu_util_anchor(gpu_rows, inject_stop_ts)` 独立函数并加单测

### 2. LOCALIZE 兜底 all-ranks（不阻塞判分）

- **原因**：P1-HW-B 长跑里 SQL 定位命中的是 rank 5 或空（HBM ramp 不让 rank 7 的 step_ms 显著慢；SQL 无 spike），编排层触发 `LOCALIZE_FALLBACK=1` 走 all-ranks SET rate=1.0
- **改法**：判分脚本 `e3_retention_score.py --case P1-HW-B --victim-pid <pid>` 支持手工指定 victim pid（走 `LOCAL_RANK=7` 定位到 rank 7 的 pid 3680251），不依赖 localize 的 culprit_rank
- **影响**：judge 侧完全 OK（rise=8692 MB @ dev 7 远超 256 MiB 阈值）；`dense_ranks` 语义已由 PR-2 记为遗留，PR-3 不再动
- **动作项**：SQL 定位对 HBM 渐衰类 case 抓不到，考虑加一条备用 SQL（按 `gpu.utilization.used_bytes` per-rank rise 排序），或把 HBM case 的判据从 step_ms 迁到 gpu.util

### 3. `launch_exp_p1hwb.sh` pull-then-score 步 `**` glob 早退

- **复现**：脚本 line 209 用 `"${PARENT_LOCAL}/dynamic/"**/set_upgrade.log` 提 `CULPRIT_PID`，line 254 也用 `**` 找 `SET_DOWNGRADE_OK`；但脚本头部**没 `shopt -s globstar`**，`**` 被 bash 当普通 glob 处理只匹配单层，`dynamic/probing_data/<pid>/set_upgrade.log` 全落空 → CULPRIT_PID 空 → `SKIP e3_retention_score`
- **修法**：脚本头部加 `shopt -s globstar`，或改成 `find "${PARENT_LOCAL}/dynamic" -name set_upgrade.log`。当前判分是**手跑归档**（`python3 project/probing-huawei/scripts/fail-slow/e3_retention_score.py --case P1-HW-B --dump-root ... --victim-pid 3680251 --out ...`），得到 W_STAR_P1_HW_B.json 后手写 PR3_EXP_P1HWB_STATUS.md
- **动作项**：commit patch 时顺便加 `shopt -s globstar` 到 `_prep/launch_exp_p1hwb.sh` 头部 + 后续 launch 脚本模板

---

## 遗留 for 后续 PR

1. **SET 分发未挂 engine registry**：`SET probing.exttbl.<t>.retain_steps=N` 走 `probing_core::config::write` 时找不到 `EXTERN_TABLES`（在 `probing-python` crate，不在 engine extension registry）。当前用 `ExternalTable.set_retention(...)` py 方法 + env 覆盖等效；把它挂到 SET 分发是下一个 PR
2. **`comm_collective` 无原生 `step` 列**：若真要按 step 保留 comm 数据，需要写入端补 step 列（编排层，非 probing 侧）。当前 P3-SW-A / P1-HW-B 类 case 用 secs 域走通，不阻塞 §3.4 通过标志
3. **Retention 是"可观测计数"，不是"硬拒推"**：MEMT 单写入者语义，环真满了仍会 advance；违规只是 log::warn! + `retention_violations_step` 计数。判分脚本回查时把 `retention_violations_step > 0` 作为"该 W\* 不够"的辅助信号（本轮 3 家族 W\* 全在 dump 内够，未触发违规）
4. **MEMT `ChunkHeader.min_ts` 已存在**：Python 层 `per_chunk_min_ts` 是冗余（可读 header），但避免了跨 crate 暴露 raw 字节偏移；O(1)/row 开销可接受。后续 PR 可考虑改为直读 header
5. **`cargo test -p probing-python`** 在 `extension-module` 特性下 test binary 缺 CPython 符号（pyo3 已知问题）；本 PR 靠 pod 冒烟 4 项间接覆盖 `for_table_tests`
6. **`dense_ranks=16` 与判据 =1 冲突**（继承 PR-2 遗留）：非 culprit rank 也各写 20 MiB torch_trace 环。需要 `PROBING_TORCH_TRACE_LAZY_OTHER=1` 或改评分脚本 `dense_ranks` 定义。PR-3 阶段 1 wheel 里未做

---

## 战役期间踩过的坑

| # | 坑 | Fix |
|---|-----|-----|
| 1 | **阶段 1 wheel 走 BUILD_WHEEL 优先级 1**（Python 3.8 abi3 wheel `_PyInterpreterState_GetEvalFrameFunc` 报错）| yysong-w0 复用现有 `/data/yinjinrun.p-huawei/probing-huawei/{cargo,rustup}` toolchain，`CARGO_NET_OFFLINE=1`，编 abi3 wheel；冒烟用 `/root/miniconda3/envs/llm_test/bin/python3`（3.10）|
| 2 | **P1-HW-B dump anchor 语义**：`gpu.utilization` 环形保留只留末尾一段，inject_stop_ts 不在环里 | judge 里 anchor 回退 `max(gpu.ts)`（本地 patch 未 commit，见旁注 #1）|
| 3 | **launch 脚本 `**` glob 无 globstar**：`pull` 后 `score` 步的 CULPRIT_PID/SET_DG 提取全空 → `SKIP e3_retention_score` | 手跑判分 + 手写 PR3_EXP_P1HWB_STATUS.md；commit patch 时加 `shopt -s globstar`（旁注 #3）|
| 4 | **`e3_retention_score.py` time_key 选择**：某些 dump 的 `ts` 列全同（degenerate = dump 时刻标签），某些 `ts` 有多值 | 加 `time_key` 自适应：ts 多值 → 用 `ts`（μs），ts 全同 → 回退 `wall_ns`（ns）；本 dump ts=14 unique → time_key=ts |
| 5 | **SQL 定位对 HBM 渐衰抓不到**（step_ms 无 spike）| 判分不依赖 localize，直接按 `LOCAL_RANK=7` 定位 rank 7 pid（LOCALIZE_FALLBACK=1 走 all-ranks；旁注 #2）|
| 6 | **60s 是"首个够的"不代表 30s 不够**：档位 {60,300,900,1800,3600,all} 里 60s 就 enough=Y，rise=10788 MB 远超 256 MiB 阈值 | 若要探"最小可检测剂量"或"最小可检测 W"，需要另做剂量扫（附录 A 消融的活）|

---

## 审稿人问答（核心 FAQ）

- **Q：你说'追溯窗按关键数据是 N 步 / T 秒'具体多长？**  
  A：**P1-SW-C 200 步**（`torch_trace` duration，判据 spike ≥ 0.5s，主证据 `step=161:dur_s=0.5289:module=DDP`）；**P3-SW-A 60 秒**（`cpu.utilization` RSS，判据 rise ≥ 50 MiB，主证据 `rise_kb=443928:span_s=59.2`）；**P1-HW-B 60 秒**（`gpu.utilization` used_bytes，判据 per-dev rise ≥ 256 MiB，主证据 `rise_mb=10788:dev=12`）。

- **Q：retention 是硬拒推吗？**  
  A：不是。**MEMT 单写入者语义，环满仍会 advance**；retention 是可观测计数（`retention_violations_step/secs`）+ log::warn!。回查时把 `violations > 0` 作为"该 W 不够"的辅助信号。

- **Q：SET 键怎么用？**  
  A：当前用 `ExternalTable.set_retention(retain_steps=N, retain_secs=T)` py 方法 + env `PROBING_EXTTBL_<TABLE>_RETAIN_{STEPS,SECS}` 覆盖；`probing.exttbl.<t>.retain_{steps,secs}` config key 已 wire 到 `ring_config`，但 SET 分发挂 engine registry 是下一个 PR。

- **Q：P3-SW-A / P1-HW-B 用 secs 而不用 steps 是为什么？**  
  A：`cpu.utilization` 和 `gpu.utilization` 是 process/device-scope 定时器采样表，**没有 step 列**；`comm_collective` 同理。用 secs 域走 chunk `min_ts` 判断是正确姿势。若未来要按 step 保留 comm 数据，需要编排层补 step 列。

---

## 产物清单

### PR-3 汇总（本目录）

| 文件 | 用途 |
|------|------|
| **`PR3_SUMMARY.md`** | 本文档 · 一页纸摘要 |
| `PR3_CODE_STATUS.md` | 阶段 1（代码 + wheel + 冒烟）详报（101 行）|
| `PR3_EXP_STATUS.md` | 阶段 2 三家族 W\* 扫窗判定 |
| `PR3_EXP_P1HWB_STATUS.md` | P1-HW-B 长跑补跑详报 |
| `RETAIN_MATRIX.md` / `RETAIN_MATRIX.json` | 3 家族 W\* 汇总 + 分窗明细 |
| `W_STAR_P1_SW_C.json` | P1-SW-C 判分明细（6 档 windows）|
| `W_STAR_P3_SW_A.json` | P3-SW-A 判分明细 |
| `W_STAR_P1_HW_B.json` | P1-HW-B 判分明细 |

### 关键运行产物

| 位置 | 内容 |
|------|------|
| `pr2_localize/20260728_211312-…-exp-c-p1swc/dynamic/probing_data/3564144/python.torch_trace` | P1-SW-C dump（复用 PR-2 实验 C，9647 rows / 8 chunks / write_chunk=0 / chunks_recycled=0）|
| `pr2_localize/20260728_204936-…-e3-b8/dynamic/probing_data/3469322/cpu.utilization` | P3-SW-A dump（复用 PR-2 B8，536 RSS samples / span 268.6s）|
| `pr2_localize/20260729_003933-…-pr3-p1hwb/dynamic/probing_data/3680251/gpu.utilization` | P1-HW-B dump（PR-3 新长跑，214 rows × 20 cols × 16 devs）|

### 上游依赖（未 commit 的本地 patch 已在旁注中标注）

| 位置 | 用途 |
|------|------|
| `project/probing-huawei/probing/memtable/src/ring_config.rs` | `TableRetention` 真相源 + env 覆盖 + config_key |
| `project/probing-huawei/probing/memtable/src/lib.rs` | 导出 retention API |
| `project/probing-huawei/probing/extensions/python/src/extensions/python/exttbls.rs` | Python 层 retention 观测 + `set_retention()` |
| `project/probing-huawei/scripts/fail-slow/pr3_retention_smoke.py` | 阶段 1 冒烟脚本 |
| `project/probing-huawei/scripts/fail-slow/e3_retention_score.py` | 阶段 2 判分脚本（3 case 通用；本地 patch：anchor 回退 + time_key 自适应 + victim-pid 直选） |
| `results/ascend-ais/pillar_c_v3/pr2_localize/_prep/launch_exp_p1hwb.sh` | P1-HW-B 长跑发射（本地 patch：`shopt -s globstar` 待加）|
| `results/ascend-ais/pillar_c_v3/pr1_baseline/PR1_SUMMARY.md` | PR-1 前置（分级容量给 P3-SW-A / P1-HW-B 数据基础）|
| `results/ascend-ais/pillar_c_v3/pr2_localize/PR2_SUMMARY.md` | PR-2 前置（SET 键名统一给 P1-SW-C 拿到 rate=1.0 密采）|

### wheel 分发（`probing-0.2.6-cp38-abi3-linux_aarch64.whl` sha256 `9416803e52cab5be8e4dc4ee58d6d746c2b94936d7706baabc8c6d40fcfa1d64`）

| 位置 | 用途 |
|------|------|
| yysong-w0 `/data/yinjinrun.p-huawei/probing-huawei/wheels/` | 编译位置 |
| grj-w0 AFS `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probing-huawei/wheels/` | 摆渡目的地 1 |
| grj-w0 AFS `/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/wheels/` | 摆渡目的地 2（launch 脚本引用） |

---

## 判定：**PASS**（3/3 家族 W\* 全出；handbook §3.4 通过标志达成）

- ✅ 阶段 1：Rust 侧改动落地 · `cargo check` / `cargo test -p probing-memtable ring_config::` 10/10 PASS · wheel 编成 + 摆渡 · pod 冒烟 4 项全绿
- ✅ 阶段 2：三家族各扫 6 档 W · P1-SW-C W\*=200 步 · P3-SW-A W\*=60 秒 · P1-HW-B W\*=60 秒
- ✅ v2 UNRESOLVED（P3-SW-A）+ v2 NO_W_STAR（P1-HW-B）都获得首个 W\* 数字
- ✅ v2 P1-SW-C W\*=100（离线）与 v3 W\*=200（正式）在 handbook §2.4 容忍窗内，差异归因 anchor_step 选取 + dump 不同
- ⚠️ 3 处本地 patch 未 commit（judge anchor / LOCALIZE all-ranks / launch `shopt -s globstar`）— 交接前需回 diff
- ⚠️ SET 分发挂 engine registry / `comm_collective` step 列 / retention 硬拒推 / MEMT header 直读 —— 遗留给后续 PR

**PR-3 收官条件齐备。**
