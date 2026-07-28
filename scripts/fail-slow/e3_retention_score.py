#!/usr/bin/env python3
"""PR-3 追溯窗扫描：3 case 各扫 6 个 W，产出 W_STAR_<case>.json + RETAIN_MATRIX.md。

设计（handbook §3.4）：
  - 一次跑最大 retain（"全程"），dump 后**离线截窗**判分（不改 memtable 内核）。
  - 语义：`retain_steps=N` 保留 (anchor_step - N, anchor_step]；`retain_secs=T` 保留 (anchor_ts - T*1e6, anchor_ts]。
  - anchor：默认 `inject_stop=300`（Loud 注入窗结束），对齐 v2 e1_offline。
  - v2 e1_offline_window_score.py 保持不动；此脚本复用 read_memt(). judge_* 保留 v2 阈值。

用法：
  python3 e3_retention_score.py \
    --case P1-SW-C --dump <dump-root> \
    --victim-pid 3564144 --out <out-dir>

或全跑：
  python3 e3_retention_score.py --scan-all --out <out-dir>

case = P1-SW-C: retain_steps ∈ {25,50,100,200,500,None(全程)}，判 torch_trace.duration 尖刺
case = P3-SW-A: retain_secs ∈ {60,300,900,1800,3600,None}，判 cpu.utilization RSS 抬升
case = P1-HW-B: retain_secs ∈ {60,300,900,1800,3600,None}，判 gpu.utilization allocated 抬升
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# 复用 v2 的 MEMT parser + case judge
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1_offline_window_score import (  # noqa: E402
    INJECT_ONSET,
    INJECT_STOP,
    RSS_ABS_THR_KB,
    RSS_RISE_THR_KB,
    HBM_RISE_THR_MB,
    SPIKE_ABS_SEC,
    SPIKE_DUR_RATIO,
    judge_p1_hw_b,
    judge_p1_sw_c,
    median,
    read_memt,
    step_bounds,
    truncate_by_w,
)

# retain window 集合（handbook §3.4）
RETAIN_STEPS_SET: list[Optional[int]] = [25, 50, 100, 200, 500, None]
RETAIN_SECS_SET: list[Optional[int]] = [60, 300, 900, 1800, 3600, None]


# ──────────────────────────────────────────────────────────────────────
# 判分：P3-SW-A · cpu.utilization RSS 抬升（重写 v2 的 judge_p3_sw，取时间窗切法）
# ──────────────────────────────────────────────────────────────────────

def judge_p3_sw_a_rss_time(
    rss_series: list[tuple[int, int]],
    anchor_ts_us: int,
    retain_secs: Optional[int],
) -> dict[str, Any]:
    """P3-SW-A：RSS in (anchor - T*1e6, anchor] 是否显示 ≥50MiB 抬升。

    rss_series: [(ts_us, rss_kb), ...]（已按 ts 升序）
    """
    if not rss_series:
        return {
            "enough": False,
            "evidence": "cpu_utilization_dump_missing",
            "primary": "cpu.utilization_rss",
            "n_rss_rows": 0,
        }
    if retain_secs is None:
        cut = [(t, v) for t, v in rss_series if t <= anchor_ts_us]
    else:
        lo = anchor_ts_us - int(retain_secs) * 1_000_000
        cut = [(t, v) for t, v in rss_series if lo < t <= anchor_ts_us]
    if len(cut) < 2:
        return {
            "enough": False,
            "evidence": f"too_few_rss_samples_in_window:{len(cut)}",
            "primary": "cpu.utilization_rss",
            "n_rss_rows": len(cut),
        }
    vals = [v for _, v in cut]
    mx, mn = max(vals), min(vals)
    rise = mx - mn
    span_s = (cut[-1][0] - cut[0][0]) / 1e6
    if rise >= RSS_RISE_THR_KB:
        return {
            "enough": True,
            "evidence": (
                f"cpu.utilization_rss:rise_kb={rise}:max_kb={mx}"
                f":n={len(cut)}:span_s={span_s:.1f}"
            ),
            "primary": "cpu.utilization_rss",
            "n_rss_rows": len(cut),
        }
    return {
        "enough": False,
        "evidence": (
            f"rss_flat_in_window:rise_kb={rise}:max_kb={mx}"
            f":min_kb={mn}:n={len(cut)}:span_s={span_s:.1f}"
        ),
        "primary": "cpu.utilization_rss",
        "n_rss_rows": len(cut),
    }


# ──────────────────────────────────────────────────────────────────────
# 判分：P1-HW-B · gpu.utilization HBM used_bytes 抬升
# ──────────────────────────────────────────────────────────────────────

def judge_p1_hw_b_gpu(
    gpu_rows: list[dict[str, Any]],
    anchor_ts_us: int,
    retain_secs: Optional[int],
) -> dict[str, Any]:
    """P1-HW-B：gpu.utilization used_bytes (per device) 窗内是否抬升 ≥ 256 MiB。

    Ascend gpu.utilization memtable 落盘时 `ts` 列常常是 dump wall time（同一批 rows 同 ts），
    真正的 per-sample 相对时钟在 `wall_ns` 列（monotonic since process start）。
    因此选择时间键的顺序：
      1) `ts` 列多值（唯一值 > 1） → 用 `ts`（微秒），anchor 为 `anchor_ts_us`。
      2) 否则退回 `wall_ns`（纳秒），anchor 取 rows 里最大 `wall_ns`（dump 瞬间）。
    """
    if not gpu_rows:
        return {
            "enough": False,
            "evidence": "gpu_utilization_dump_missing",
            "primary": "gpu.utilization_used_bytes",
            "n_gpu_rows": 0,
        }
    ts_set = {int(r.get("ts") or 0) for r in gpu_rows}
    use_wall = len(ts_set) <= 1
    if use_wall:
        time_key = "wall_ns"
        anchor_time = max((int(r.get("wall_ns") or 0) for r in gpu_rows), default=0)
        secs_scale = 1_000_000_000  # ns
        primary_note = "gpu.utilization_used_bytes(wall_ns)"
    else:
        time_key = "ts"
        anchor_time = anchor_ts_us
        secs_scale = 1_000_000  # us
        primary_note = "gpu.utilization_used_bytes"
    if retain_secs is None:
        cut = [r for r in gpu_rows if int(r.get(time_key) or 0) <= anchor_time]
    else:
        lo = anchor_time - int(retain_secs) * secs_scale
        cut = [
            r for r in gpu_rows
            if lo < int(r.get(time_key) or 0) <= anchor_time
        ]
    if len(cut) < 3:
        return {
            "enough": False,
            "evidence": f"too_few_gpu_samples_in_window:{len(cut)}:time_key={time_key}",
            "primary": primary_note,
            "n_gpu_rows": len(cut),
        }
    # per-device max used_bytes ramp
    by_dev: dict[int, list[tuple[int, int]]] = {}
    for r in cut:
        try:
            dev = int(r.get("device_id") or 0)
            t = int(r.get(time_key) or 0)
            used = int(r.get("used_bytes") or 0)
        except Exception:
            continue
        by_dev.setdefault(dev, []).append((t, used))
    if not by_dev:
        return {
            "enough": False,
            "evidence": "gpu_rows_missing_used_bytes",
            "primary": primary_note,
            "n_gpu_rows": len(cut),
        }
    best_rise_mb = 0.0
    best_dev = -1
    for dev, ser in by_dev.items():
        vals = [u for _, u in ser]
        if len(vals) < 2:
            continue
        rise_mb = (max(vals) - min(vals)) / (1024 * 1024)
        if rise_mb > best_rise_mb:
            best_rise_mb = rise_mb
            best_dev = dev
    enough = best_rise_mb >= HBM_RISE_THR_MB
    return {
        "enough": enough,
        "evidence": (
            f"gpu.utilization_used_bytes:rise_mb={best_rise_mb:.1f}"
            f":dev={best_dev}:n_devs={len(by_dev)}:n_rows={len(cut)}"
            f":time_key={time_key}"
        ),
        "primary": primary_note,
        "n_gpu_rows": len(cut),
    }


# ──────────────────────────────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────────────────────────────

@dataclass
class DumpPaths:
    """定位一个 dump 里的 victim pid 目录。"""
    dump_root: Path
    victim_pid: str

    def torch_trace(self) -> Path:
        return self.dump_root / self.victim_pid / "python.torch_trace"

    def cpu_utilization(self) -> Path:
        return self.dump_root / self.victim_pid / "cpu.utilization"

    def gpu_utilization(self) -> Path:
        return self.dump_root / self.victim_pid / "gpu.utilization"


def find_probing_data_root(dump_dir: Path) -> Optional[Path]:
    """从 dump 根目录找到 probing_data 目录。"""
    if (dump_dir / "probing_data").is_dir():
        return dump_dir / "probing_data"
    hits = list(dump_dir.rglob("probing_data"))
    for h in hits:
        if h.is_dir() and any(h.iterdir()):
            return h
    return None


def load_torch_trace_rows(pid_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    p = pid_dir / "python.torch_trace"
    if not p.exists():
        return [], {"path": str(p), "error": "missing"}
    tab = read_memt(p)
    return tab.rows, tab.meta


def load_cpu_rss(pid_dir: Path) -> list[tuple[int, int]]:
    """返回 [(ts_us, rss_kb), ...]（scope=process, 按 ts 升序）。"""
    p = pid_dir / "cpu.utilization"
    if not p.exists():
        return []
    tab = read_memt(p)
    out = []
    for r in tab.rows:
        scope = str(r.get("scope") or "")
        if scope != "process":
            continue
        try:
            ts = int(r.get("ts") or 0)
            rss = int(r.get("rss_kb") or 0)
        except Exception:
            continue
        if rss > 0:
            out.append((ts, rss))
    out.sort(key=lambda x: x[0])
    return out


def load_gpu_used(pid_dir: Path) -> list[dict[str, Any]]:
    p = pid_dir / "gpu.utilization"
    if not p.exists():
        return []
    tab = read_memt(p)
    rows = list(tab.rows)
    rows.sort(key=lambda r: int(r.get("ts") or 0))
    return rows


def compute_anchor_ts(
    torch_rows: list[dict[str, Any]],
    anchor_step: int = INJECT_STOP,
) -> Optional[int]:
    """从 torch_trace 找 local_step==anchor_step 的最大 ts (us)。若找不到则退回 <=anchor 的最大 ts。"""
    candidates_le = []
    exact = []
    for r in torch_rows:
        try:
            step = int(r.get("local_step"))
            ts = int(r.get("timestamp") or 0)
        except Exception:
            continue
        if ts <= 0:
            continue
        if step == anchor_step:
            exact.append(ts)
        if step <= anchor_step:
            candidates_le.append(ts)
    if exact:
        return max(exact)
    if candidates_le:
        return max(candidates_le)
    return None


# ──────────────────────────────────────────────────────────────────────
# case 扫描
# ──────────────────────────────────────────────────────────────────────

def scan_p1_sw_c(pid_dir: Path) -> dict[str, Any]:
    rows, meta = load_torch_trace_rows(pid_dir)
    if not rows:
        return {"case": "P1-SW-C", "status": "BLOCKED", "reason": "no_torch_trace", "meta": meta}
    mn, mx, steps = step_bounds(rows)
    if INJECT_STOP in steps:
        anchor = INJECT_STOP
    else:
        le = [s for s in steps if s <= INJECT_STOP]
        anchor = max(le) if le else mx
    win_rows = []
    w_star = None
    primary_evidence = None
    for w in RETAIN_STEPS_SET:
        win = truncate_by_w(rows, w, anchor_step=anchor)
        j = judge_p1_sw_c(win, w)
        label = "all" if w is None else str(w)
        row = {
            "W": label,
            "retain_steps": w,
            "enough": bool(j["enough"]),
            "evidence": j.get("evidence"),
            "primary": j.get("primary"),
            "n_tt_rows": j.get("n_tt_rows"),
            "n_tt_steps": j.get("n_tt_steps"),
            "anchor_step": anchor,
        }
        win_rows.append(row)
        if j["enough"] and w_star is None:
            w_star = w  # 可为 None if only "all" pass
            primary_evidence = j.get("evidence")
    status = "OK" if w_star is not None else (
        "OK" if any(r["enough"] for r in win_rows) else "NO_W_STAR"
    )
    if any(r["enough"] for r in win_rows) and w_star is None:
        # w_star=None 但 "all" 覆盖了 → 记为 all
        for row in win_rows:
            if row["enough"]:
                w_star = None if row["retain_steps"] is None else row["retain_steps"]
                primary_evidence = row["evidence"]
                break
    return {
        "case": "P1-SW-C",
        "status": status,
        "retain_unit": "steps",
        "w_star_steps": w_star,
        "primary_evidence": primary_evidence,
        "anchor_step": anchor,
        "step_min": mn,
        "step_max": mx,
        "n_unique_steps": len(steps),
        "n_torch_trace_rows": len(rows),
        "torch_trace_meta": meta,
        "windows": win_rows,
    }


def scan_p3_sw_a(pid_dir: Path) -> dict[str, Any]:
    rss = load_cpu_rss(pid_dir)
    torch_rows, tt_meta = load_torch_trace_rows(pid_dir)
    if not rss:
        return {
            "case": "P3-SW-A",
            "status": "BLOCKED",
            "reason": "no_cpu_utilization_or_no_rss_process_rows",
        }
    # anchor_ts: 优先取 torch_trace inject_stop 对应 ts；否则 rss 末端 ts
    anchor_ts = None
    if torch_rows:
        anchor_ts = compute_anchor_ts(torch_rows, INJECT_STOP)
    if anchor_ts is None:
        anchor_ts = rss[-1][0]
    win_rows = []
    w_star = None
    primary_evidence = None
    for w in RETAIN_SECS_SET:
        j = judge_p3_sw_a_rss_time(rss, anchor_ts, w)
        label = "all" if w is None else f"{w}s"
        row = {
            "W": label,
            "retain_secs": w,
            "enough": bool(j["enough"]),
            "evidence": j.get("evidence"),
            "primary": j.get("primary"),
            "n_rss_rows": j.get("n_rss_rows"),
        }
        win_rows.append(row)
        if j["enough"] and w_star is None:
            w_star = w
            primary_evidence = j.get("evidence")
    if any(r["enough"] for r in win_rows) and w_star is None:
        for row in win_rows:
            if row["enough"]:
                w_star = None if row["retain_secs"] is None else row["retain_secs"]
                primary_evidence = row["evidence"]
                break
    status = "OK" if any(r["enough"] for r in win_rows) else "NO_W_STAR"
    total_span_s = (rss[-1][0] - rss[0][0]) / 1e6 if rss else 0.0
    rss_min = min(v for _, v in rss)
    rss_max = max(v for _, v in rss)
    return {
        "case": "P3-SW-A",
        "status": status,
        "retain_unit": "secs",
        "w_star_secs": w_star,
        "primary_evidence": primary_evidence,
        "anchor_ts_us": anchor_ts,
        "n_rss_samples_total": len(rss),
        "rss_kb_min_total": rss_min,
        "rss_kb_max_total": rss_max,
        "rss_kb_rise_total": rss_max - rss_min,
        "rss_span_s_total": total_span_s,
        "windows": win_rows,
    }


def scan_p1_hw_b(pid_dir: Path) -> dict[str, Any]:
    gpu = load_gpu_used(pid_dir)
    torch_rows, _ = load_torch_trace_rows(pid_dir)
    if not gpu:
        return {
            "case": "P1-HW-B",
            "status": "BLOCKED",
            "reason": "no_gpu_utilization",
        }
    # Anchor logic for P1-HW-B：优先 torch_trace inject_stop 的 timestamp（对齐注入语义）；
    # 但 gpu.utilization 是环形保留，可能只留 [dump - retain_secs, dump] 一段，不包含
    # 早期 inject_stop 时刻。因此若 gpu 所有 ts 都在 inject_stop_ts 之后，回退到
    # max(gpu.ts) 作 anchor（== dump 时刻），代表"retention 窗口反查到 W 秒前"。
    anchor_ts = None
    inject_stop_ts = None
    if torch_rows:
        inject_stop_ts = compute_anchor_ts(torch_rows, INJECT_STOP)
    max_gpu_ts = max((int(r.get("ts") or 0) for r in gpu), default=0)
    if inject_stop_ts is not None and max_gpu_ts and max_gpu_ts <= inject_stop_ts + 1_000_000:
        # gpu ts 早于或对齐 inject_stop → 直接用 inject_stop 做 anchor（原语义）
        anchor_ts = inject_stop_ts
    elif max_gpu_ts:
        # gpu.utilization 全部在 inject_stop 之后（ring 只留末尾）→ 用 dump 时刻做 anchor
        anchor_ts = max_gpu_ts
    else:
        anchor_ts = inject_stop_ts or 0
    win_rows = []
    w_star = None
    primary_evidence = None
    for w in RETAIN_SECS_SET:
        j = judge_p1_hw_b_gpu(gpu, anchor_ts, w)
        label = "all" if w is None else f"{w}s"
        row = {
            "W": label,
            "retain_secs": w,
            "enough": bool(j["enough"]),
            "evidence": j.get("evidence"),
            "primary": j.get("primary"),
            "n_gpu_rows": j.get("n_gpu_rows"),
        }
        win_rows.append(row)
        if j["enough"] and w_star is None:
            w_star = w
            primary_evidence = j.get("evidence")
    if any(r["enough"] for r in win_rows) and w_star is None:
        for row in win_rows:
            if row["enough"]:
                w_star = None if row["retain_secs"] is None else row["retain_secs"]
                primary_evidence = row["evidence"]
                break
    status = "OK" if any(r["enough"] for r in win_rows) else "NO_W_STAR"
    return {
        "case": "P1-HW-B",
        "status": status,
        "retain_unit": "secs",
        "w_star_secs": w_star,
        "primary_evidence": primary_evidence,
        "anchor_ts_us": anchor_ts,
        "n_gpu_samples_total": len(gpu),
        "windows": win_rows,
    }


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────

CASE_DISPATCH = {
    "P1-SW-C": scan_p1_sw_c,
    "P3-SW-A": scan_p3_sw_a,
    "P1-HW-B": scan_p1_hw_b,
}


def render_matrix_md(results: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# PR-3 追溯窗矩阵 (handbook §3.4)",
        "",
        "> 追溯窗**不是**一个数字，是每种关键数据各一个。",
        "> W* = 首次 enough=Y 的最小保留窗；all=全程仍不够 → NO_W_STAR。",
        "",
        "## W* 总表",
        "",
        "| case | 关键数据 | retain 单位 | W\\* | 主证据 |",
        "|---|---|---|---:|---|",
    ]
    for case in ("P1-SW-C", "P3-SW-A", "P1-HW-B"):
        r = results.get(case, {})
        if not r or r.get("status") == "BLOCKED":
            lines.append(
                f"| {case} | — | — | — | BLOCKED: {r.get('reason','no dump')} |"
            )
            continue
        unit = r.get("retain_unit", "?")
        w = r.get("w_star_steps") if unit == "steps" else r.get("w_star_secs")
        w_label = "all(全程)" if w is None and r.get("status") == "OK" else (
            "—" if r.get("status") != "OK" else f"{w}{'步' if unit=='steps' else '秒'}"
        )
        primary = r.get("primary_evidence") or ""
        key_data = {
            "P1-SW-C": "python.torch_trace duration",
            "P3-SW-A": "cpu.utilization RSS",
            "P1-HW-B": "gpu.utilization used_bytes",
        }.get(case, "?")
        lines.append(f"| {case} | {key_data} | {unit} | {w_label} | `{primary[:100]}` |")
    lines += [
        "",
        "## 结论",
        "",
        "**追溯窗按关键数据分别是：**",
    ]
    # Concluding sentence
    p1 = results.get("P1-SW-C", {})
    p3 = results.get("P3-SW-A", {})
    p1hw = results.get("P1-HW-B", {})

    def _fmt(r: dict, unit: str) -> str:
        if not r or r.get("status") == "BLOCKED":
            return "N/A (dump missing)"
        w = r.get("w_star_steps") if unit == "steps" else r.get("w_star_secs")
        if w is None and r.get("status") == "OK":
            return f"all(全程 {'≥' + str(RETAIN_STEPS_SET[-2]) if unit=='steps' else '≥3600s'})"
        if r.get("status") != "OK":
            return "NO_W_STAR"
        return f"{w} {'步' if unit=='steps' else '秒'}"

    lines.append(f"- **P1-SW-C** (torch_trace duration): {_fmt(p1, 'steps')}")
    lines.append(f"- **P3-SW-A** (cpu.utilization RSS): {_fmt(p3, 'secs')}")
    lines.append(f"- **P1-HW-B** (gpu.utilization used_bytes): {_fmt(p1hw, 'secs')}")
    lines += [
        "",
        "## 分窗明细",
        "",
    ]
    for case in ("P1-SW-C", "P3-SW-A", "P1-HW-B"):
        r = results.get(case, {})
        if not r:
            continue
        lines.append(f"### {case}")
        lines.append("")
        if r.get("status") == "BLOCKED":
            lines.append(f"- **BLOCKED**：{r.get('reason')}")
            lines.append("")
            continue
        lines.append(f"- status: **{r.get('status')}**")
        if r.get("anchor_step") is not None:
            lines.append(f"- anchor_step: `{r.get('anchor_step')}`")
        if r.get("anchor_ts_us") is not None:
            lines.append(f"- anchor_ts_us: `{r.get('anchor_ts_us')}`")
        lines.append("")
        lines.append("| W | enough | n_rows | evidence |")
        lines.append("|---|:---:|---:|---|")
        for w in r.get("windows") or []:
            n = w.get("n_tt_rows") or w.get("n_rss_rows") or w.get("n_gpu_rows") or 0
            lines.append(
                f"| {w['W']} | {'Y' if w['enough'] else 'N'} | {n} | `{w.get('evidence')}` |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=list(CASE_DISPATCH.keys()))
    ap.add_argument("--dump-root", type=Path, help="dir containing probing_data/<pid>/...")
    ap.add_argument("--victim-pid", type=str, help="pid subdir under probing_data")
    ap.add_argument("--scan-all", action="store_true", help="ignore --case/--dump; use built-in map")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--map",
        type=Path,
        default=None,
        help="json {case: {dump_root, victim_pid}} for --scan-all"
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    dumps: dict[str, dict[str, Any]] = {}
    if args.scan_all:
        if args.map and args.map.exists():
            dumps = json.loads(args.map.read_text())
        else:
            # built-in map
            base_v3 = Path(
                "/Users/yinjinrun/Codespace/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize"
            )
            dumps = {
                "P1-SW-C": {
                    "dump_root": str(
                        base_v3 / "20260728_211312-pillar-c-v3-pr2-exp-c-p1swc/dynamic/probing_data"
                    ),
                    "victim_pid": "3564144",
                },
                "P3-SW-A": {
                    "dump_root": str(
                        base_v3 / "20260728_204936-pillar-c-v3-pr2-e3-b8/dynamic/probing_data"
                    ),
                    "victim_pid": "3469322",
                },
                # P1-HW-B: no dump available; leave blank
                "P1-HW-B": {
                    "dump_root": "",
                    "victim_pid": "",
                },
            }
    else:
        if not (args.case and args.dump_root and args.victim_pid):
            ap.error("--case/--dump-root/--victim-pid required (or use --scan-all)")
        dumps[args.case] = {
            "dump_root": str(args.dump_root),
            "victim_pid": args.victim_pid,
        }

    results: dict[str, dict[str, Any]] = {}
    for case, cfg in dumps.items():
        fn = CASE_DISPATCH.get(case)
        if fn is None:
            continue
        droot = cfg.get("dump_root") or ""
        pid = cfg.get("victim_pid") or ""
        if not droot or not pid:
            results[case] = {
                "case": case,
                "status": "BLOCKED",
                "reason": "no dump provisioned (see handbook §3.4 / defer)",
            }
            continue
        pid_dir = Path(droot) / pid
        if not pid_dir.is_dir():
            results[case] = {
                "case": case,
                "status": "BLOCKED",
                "reason": f"pid dir missing: {pid_dir}",
            }
            continue
        print(f"[e3-retain] scoring {case} @ {pid_dir}", flush=True)
        r = fn(pid_dir)
        r["dump_root"] = droot
        r["victim_pid"] = pid
        results[case] = r
        print(
            f"  status={r.get('status')} "
            f"w_star={r.get('w_star_steps') or r.get('w_star_secs')}",
            flush=True,
        )
        # 个 case json
        wf = args.out / f"W_STAR_{case.replace('-','_')}.json"
        wf.write_text(json.dumps(r, indent=2, ensure_ascii=False) + "\n")

    (args.out / "RETAIN_MATRIX.md").write_text(render_matrix_md(results))
    (args.out / "RETAIN_MATRIX.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"[e3-retain] wrote {args.out}/RETAIN_MATRIX.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
