# PR-2 B6 代码交付状态

**日期**：2026-07-28
**parent**：`20260728_141052-pillar-c-v3-pr2-e3-b5d`（离线拆账 `PR2_B6_VOLUME_BREAKDOWN.md`）
**状态**：**代码已落地 + Python-only 部署 pod**，smoke 见 `PR2_EXP_B6_SMOKE.md`。

---

## 上下文

B5d headline=**115.05%**。拆账（`PR2_B6_VOLUME_BREAKDOWN.md`）：

| 组件 | 类别 | MiB | % of full |
|------|------|----:|--------:|
| main_empty `torch_step_timing` + `comm_collective` | 空环 × 15 rank | 600.0 | 35.11% |
| main_empty 周期小表（cpu/gpu × 4） | 空环 × 15 rank | 420.1 | 24.58% |
| extra_pid 全部表 | 短生命 pid × 18 | 864.1 | 50.56% |

**反事实**：只修 P1（step+comm 空环）→ 79.94%；再修 P2（extra_pid dump）→ 44%。

---

## 改动清单

| 文件 | 改动 | 触点 |
|------|------|------|
| `python/probing/core/table.py` | `@table` 增加 `lazy=True` kwarg：`init_table()` 延迟到第一次 `save()` | 装饰器 |
| `python/probing/profiling/torch_probe.py` | `TorchTrace` / `TorchStepTiming` 改 `@table(lazy=True)`；`_record_step_timing` 在 `PROBING_TORCH_STEP_TIMING_LAZY=1`（**默认 0**——见"关键设计决定 §6"）+ `rate<=0` + 非 shadow 时短路返回 | 主 gate（P1，opt-in） |
| `python/probing/profiling/collective/record.py` | `CommCollective` 改 `@table("comm_collective", lazy=True)`；`record_comm_lite` 在 `PROBING_TORCH_COMM_COLLECTIVE_LAZY=1`（默认 1）+ 当前 `probing.torch.profiling` rate=0 时短路（仍调用 `note_last_comm` 保留跨 rank cursor） | 主 gate（P1） |
| `scripts/fail-slow/prune_extra_pids.py` | 新增：dump 前按 `WORKER_PIDS_FILE` + `CULPRIT_PIDS` + `torch_step_timing/torch_trace` 数据签名，删掉 `probing_data/<pid>/` 里非主 worker 的 pid 子目录 | P2 dump 过滤 |
| `scripts/fail-slow/hold_exec_run_case.sh` | 1) SET 完成后写 `${out}/worker_pids.txt` snapshot（`_pillar_c_localize.py --list-worker-pids`）；2) `pull_results` 在 `tar cf` 之前先 `jsync` prune 脚本到 pod 并跑 dry-off 剪枝（受 `PILLAR_C_PRUNE_EXTRA_PIDS=1` 开关控制，默认开） | P2 orchestrate |

---

## 关键设计决定

1. **lazy `@table`**：`init_table()` 从"import 时立即建 mmap 环"改为"首次 `save()` 时建"。非 culprit rank 在 rate=0 阶段完全不会 `save`，就完全不落 20 MiB × N 环。
2. **不删旧行为**：所有开关默认合理值（LAZY_COMM=1，LAZY_STEP_TIMING=0），显式 env 可切换；`PILLAR_C_PRUNE_EXTRA_PIDS=0` 关掉 P2 剪枝。
3. **note_last_comm 保留**：`record_comm_lite` 短路时仍调用 Rust `note_last_comm`，保证 comm-latency 探测的跨 rank 内部游标不错位（Pillar D fanout 依赖）。
4. **周期小表未动**：cpu/gpu.utilization / cpu.tasks / gpu.hccs 由 Rust collector 直建；本轮不改（下一版 P3）。目前占 main_empty 420 MiB。
5. **不需重编 wheel**：所有主变更都是 Python 侧；Rust 侧只在 `exttbls.rs` / `ring_config.rs` 之前的 PR-1 分级容量，本次没动。
6. **STEP_TIMING gate 默认关**：`python.torch_step_timing` 是 case=P3-SW-A 的 **localize 判据来源**（`_pillar_c_localize.py` 走 `step_ms` mode），如果非 culprit rank 也不写，MAX(step_duration_sec) 就都是 0，跨 rank 挑不出 culprit。因此 STEP_TIMING lazy gate 默认 OFF；等 localize 侧也升级到 `comm_max` 或跨 rank fanout 后再打开。P1 主省能来自 `comm_collective`（300 MiB main_empty + 部分 extra_pid）。

---

## 部署状态（pod 侧）

- 目标：`yysong-worker-0:/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle/`
- 已用 `install -m 0644` 覆盖：`pydeps/probing/core/table.py`、`pydeps/probing/profiling/torch_probe.py`、`pydeps/probing/profiling/collective/record.py`
- 已用 `install -m 0755` 覆盖：`prune_extra_pids.py`
- `find __pycache__ -exec rm -rf {} +` 清 pyc 缓存

**Pod 内 import 冒烟**（TORCH_DEVICE_BACKEND_AUTOLOAD=0，llm_test conda）：
- `probing.core.table` — `table` 签名带 `lazy` kwarg ✓
- `probing.profiling.torch_probe._step_timing_lazy_enabled` — 存在 ✓
- `probing.profiling.collective.record._skip_comm_collective_on_this_rank` — 存在 ✓
- `probing.ExternalTable.get("comm_collective")` → `ValueError`（未 eager 建） ✓
- `probing.ExternalTable.get("torch_step_timing")` → `ValueError`（未 eager 建） ✓
- 一次 `TorchStepTiming(...).save()` 后 → 存在 ✓ 且 `torch_trace` 仍 lazy ✓
- Gate 参数矩阵：
  - `rate=0, COMM_LAZY=1（默认）` → skip_comm=True
  - `rate=1, COMM_LAZY=1` → skip_comm=False
  - `rate=0, COMM_LAZY=0`（回退） → skip_comm=False
  - `STEP_TIMING_LAZY` default → False（保留 B5d，localize 需要）
  - `STEP_TIMING_LAZY=1` override → True
- Prune 冒烟：manifest={1000,2000}, culprit=2000, dump={1000,2000,3000,4000,crash} → kept={1000,2000,crash}, removed={3000,4000} ✓

---

## Gate 自检

| Gate | 结果 |
|------|------|
| 三个改动都对齐 `PROBING_TORCH_*_LAZY` / `PILLAR_C_PRUNE_EXTRA_PIDS` env | ✓ |
| Python `ast.parse` 三个文件 | ✓ |
| Pod 内 `import probing.core.table` + `inspect.signature(table)` 有 `lazy` | ✓ |
| Pod 内 `probing.ExternalTable.get("torch_step_timing")` 触发 `ValueError`（未 eager 建） | ✓ |
| Pod 内 首次 save 后 `torch_step_timing` 成型，`torch_trace` 仍 lazy | ✓ |
| `prune_extra_pids.py` 干燥/湿测符合预期 | ✓ |

---

## 遗留 / 下一步

- **周期小表分级容量（P3）** — 未改；离线拆账 420 MiB 主要来自 cpu.util/tasks + gpu.util/hccs 上的常驻空环。等 B6 smoke 判 headline 决定是否派 B7。
- **cold 层**：`probing_data/<pid>/cold/*.memc` 目前不受剪枝影响；如果 pruned pid 有 cold 段，仍会删（`shutil.rmtree`）。
- **`--list-worker-pids` 竞态**：manifest 快照写在 SET 完成之后；如果训练早于此挂掉，manifest 就是空。`prune` 里有 fallback：目录含 `python.torch_step_timing`/`python.torch_trace` 就保留。所以 manifest 缺失只是"更保守剪枝"，不会误删主 worker。
- **多 pod fanout**：本 PR 仅 yysong-worker-0 单节点场景；Pillar D 多节点需要跨 pod 的 worker manifest 汇总（后续 PR）。

---

## 关键路径

- P1 gate：`python/probing/profiling/torch_probe.py::_record_step_timing`、`python/probing/profiling/collective/record.py::record_comm_lite`
- P2 gate：`scripts/fail-slow/prune_extra_pids.py`、`scripts/fail-slow/hold_exec_run_case.sh::pull_results`（新增 prune 段）+ SET 完成处 `worker_pids.txt` 快照
- Env：`PROBING_TORCH_STEP_TIMING_LAZY`（默认 1）、`PROBING_TORCH_COMM_COLLECTIVE_LAZY`（默认 1）、`PILLAR_C_PRUNE_EXTRA_PIDS`（默认 1）、`PILLAR_C_PRUNE_DRY_RUN`（默认 0）
