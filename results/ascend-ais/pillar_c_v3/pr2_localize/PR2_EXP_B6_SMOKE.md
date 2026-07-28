# PR-2 B6 短 smoke

**日期**：2026-07-28
**状态**：**PARTIAL · Pending 长跑** — code diff 已落地并在 pod 内验通了 lazy/prune 语义（细节见 `PR2_B6_CODE_STATUS.md` 冒烟段），但短 smoke 全流程本轮**未跑到 dump**。原因：`ais-cf3e61a5` 跳板反向隧道断了（`Connection closed by 8.133.166.67 port 22` / `banner exchange timeout`），本轮期间 kubectl exec 全部超时。

---

## smoke 尝试

| 项 | 值 |
|----|-----|
| parent RUN_ID | `20260728_164425-pillar-c-v3-pr2-b6-smoke` |
| 目标 pod | `yysong-worker-0` |
| ARM | `e3a_upgrade`（复用 pr2_e3a 骨架） |
| ITERS | 250（短） |
| CASE | `P3-SW-A` |
| RESIDENT_RATE | 0 |
| PILLAR_C_SET_RATE | 1.0 |
| PILLAR_C_SET_SCOPE | `localize` |
| PILLAR_C_SET_AT_STEP | 100 |
| PILLAR_C_SET_HANG_MAX_S | 180 |
| B6 gates | `PROBING_TORCH_COMM_COLLECTIVE_LAZY=1`（隐式默认）、`PROBING_TORCH_STEP_TIMING_LAZY=0`（默认，见 CODE_STATUS §6）、`PILLAR_C_PRUNE_EXTRA_PIDS=1` |

尝试路径：`run_pillar_c_arm.sh` → `hold_exec_run_case.sh` → ssh 跳板 → kubectl exec pod.

**日志进度**（`/tmp/b6_smoke_log/20260728_164425-pillar-c-v3-pr2-b6-smoke.log`）：
```
FIRE_OK
warmup ok (15s)
wait measure step 100…
measure step 100 reached (10s)
inline 8a GC/stall active (victim=7 every=1 stall=0.25)
Pillar-C SET↑ wait L>=100 (jsonl lines; NOT step marker)…
L=123 >= 100 (0s) → attach/SET
Pillar-C SET upgrade probing.torch.profiling=on,rate=1.0 scope=localize (localize→culprit SET)…
Timeout, server 127.0.0.1 not responding.  ← 跳板隧道断，测出的是 orchestration
```

Timeouts 连续，进程等 3 min 无进展；主动 kill。

---

## Pod 内单元冒烟（已通过，位于 CODE_STATUS）

- `@table(lazy=True)` 阻止 import-time mmap 创建：`ExternalTable.get("torch_step_timing"/"comm_collective")` 返回 `ValueError`。
- 首次 `.save()` 立刻在 `/tmp/b6_smoke_data/<pid>/python.torch_step_timing` 建 20 MiB 文件（sanity）。
- Gate 矩阵：
  - `_skip_comm_collective_on_this_rank()` 在 `rate=0, LAZY=1(默认)` 时 True；`rate=1` 时 False；`LAZY=0` 时 False。
  - `_step_timing_lazy_enabled()` 默认 False（保留 B5d localize step_ms 判据）；`STEP_TIMING_LAZY=1` 打开。
- `prune_extra_pids.py` 冒烟：
  - manifest={1000, 2000}、culprit=2000、fixture={1000, 2000, 3000, 4000, crash}
  - 结果：kept={1000, 2000, crash}, removed={3000, 4000}（extra_pid 无 worker signature）

即：**Python 代码路径通了**；smoke 无法完成 E2E 是**外部（跳板）问题**，不是 B6 代码问题。

---

## 五指标验收（本轮无法判）

| 项 | 目标 | 结果 |
|----|-----|-----|
| `dense_ranks` | 1 | **pending**（未到 dump） |
| `culprit_rank` | 7（GT） | **pending** |
| `LOCALIZE_FALLBACK` | 0 | **pending** |
| `SET_OK` | Y | **pending**（SET 命令下发前跳板断） |
| extra_pid dump 缩减 | 18 → ≤ 主 worker 16 + 少数 culprit | **pending**（未 pull） |
| main_empty `python.comm_collective`/`torch_step_timing` 文件 | comm 缩到 0-2 MiB、step 保持（默认 lazy=0） | **pending**（未 pull） |

---

## 头条估算（如果 B6 组合能跑）

**离线拆账参考**（`PR2_B6_VOLUME_BREAKDOWN.md`）：

| 假设 | 保留额外（MiB） | 估算 dynamic MiB | 估算 ratio |
|------|-----:|-----:|-----:|
| B5d 现状 | - | 1966.20 | **115.05%** |
| 只关 `comm_collective` at rate=0（B6 P1，main_empty + extra_pid） | 500-600 | ~1400 | ~82% |
| 加 P2 prune extra_pid | 864 | ~500 | ~30% |
| **B6 组合（P1 COMM + P2）** | ~1200 | ~700-800 | **~40-47%** |

即：**若 dump 走通，headline 预期 <50%**（离目标 <100% 有很大余量）。**不再触发 `step_timing` gate 是保守选择**，避免破坏 localize；即使不触发，仅 comm+prune 已经足够把头条打穿 100%。

---

## 判定：**PARTIAL — 代码就绪，但 smoke 环境断了**

- code diff 语义确认（pod 内单元测试全过）
- E2E dump 未完成 → 头条 pending
- 无法确认 SET_OK / dense_ranks / LOCALIZE_FALLBACK

## 下一轮建议

1. 等 `ais-cf3e61a5` 反向隧道恢复（`scripts/ais-jump/rebootstrap.md` 或本机 `curl -fsSL http://8.133.166.67/boot.sh | bash`）。
2. 隧道回来后：
   - 若时间紧：直接派 **B7 长跑**（同 B5d 参数，ITERS=1000+，加 B6 code），验头条 ratio。
   - 若时间余裕：先跑 200-step smoke，再决定长跑。
3. 若 B7 头条仍 >100%（可能性小，见上表反事实），再打开 `PROBING_TORCH_STEP_TIMING_LAZY=1`，并同步升级 localize 走 `comm_max` 兜底 mode（当前 P3-SW-A 是 step_ms，不能盲开）。
4. 若 B7 headline < 60%，可直接开始 PR-3（retention 语义）；否则先做 P3 周期小表分级容量。
