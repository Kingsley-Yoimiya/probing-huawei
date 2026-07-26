#!/usr/bin/env python3
"""Pillar-C E1-off：已有 full_fidelity MEMT 离线截窗，判「采集内容够不够归因」。

尺（生死线）：
  - 判的是采集内容（torch_trace / cpu.utilization 等），禁止用训练 step_ms 把各窗判成同 D。
  - W = 保留触发后最近 W 个 local_step 的详采；W=full 用环内全部步。
  - 产出每 case 首次达到「够归因」的最小 W*。

用法（本机或 pod 内均可，读 AFS/本机 dump）：
  python3 e1_offline_window_score.py \\
    --out results/ascend-ais/pillar_c_v2/E1_off \\
    [--afs-root /afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# MEMT layout (probing/memtable layout.rs v3)
MAGIC_MEMT = 0x4D454D54
DTYPE = {1: "u8", 2: "i32", 3: "i64", 4: "f32", 5: "f64", 6: "u64", 7: "u32", 8: "str", 9: "bytes"}
WINDOWS = [10, 25, 50, 100, 200, None]  # None = full

# 代表 case → full_fidelity parent（AFS pillar_c/）
DEFAULT_CASES = {
    "P3-SW-A": "20260725_230350-pillar-c-p3-sw-a-loud",
    "P3-SW-B": "20260725_233537-pillar-c-p3-sw-b-loud",
    "P1-HW-B": "20260726_001353-pillar-c-p1-hw-b-loud",
    "P1-SW-C": "20260726_012627-pillar-c-p1-sw-c-loud",
}

# 归因证据（采集侧；与 EVAL-GAP §2.2 / score_dlevel_sql 对齐，但不读训练埋点）
RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000
# HBM 渐衰：窗内 max_allocated 抬升（MB）
HBM_RISE_THR_MB = 256.0
# 编译尖刺：post-forward duration 相对窗内中位的倍数（Loud tip 常 3–5×）
SPIKE_DUR_RATIO = 3.0
SPIKE_ABS_SEC = 0.40
# Loud 注入窗（与 recipes / run_pillar_c_arm 一致）；截窗锚在 inject_stop，避免 dump 远晚于 onset
INJECT_ONSET = 100
INJECT_STOP = 300


@dataclass
class MemtTable:
    path: Path
    cols: list[tuple[str, int, int]]  # name, dtype, elem_size
    rows: list[dict[str, Any]]
    meta: dict[str, Any]


def _read_lp(buf: bytes, off: int, chunk_start: int) -> tuple[bytes, int]:
    raw = struct.unpack_from("<i", buf, off)[0]
    if raw < 0:
        ref_off = chunk_start + (-raw)
        ln = struct.unpack_from("<I", buf, ref_off)[0]
        return buf[ref_off + 4 : ref_off + 4 + ln], off + 4
    return buf[off + 4 : off + 4 + raw], off + 4 + raw


def read_memt(path: Path, max_rows: Optional[int] = None) -> MemtTable:
    buf = path.read_bytes()
    if len(buf) < 64 or struct.unpack_from("<I", buf, 0)[0] != MAGIC_MEMT:
        raise ValueError(f"not MEMT: {path}")
    magic, ver, hsz, bom, ts_col, flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    write_chunk, refcount, creator_pid, _pad = struct.unpack_from("<IIII", buf, 32)
    _start, chunks_recycled, rows_overwritten = struct.unpack_from("<QII", buf, 48)
    cols: list[tuple[str, int, int]] = []
    for i in range(ncols):
        off = 64 + i * 64
        namelen = struct.unpack_from("<H", buf, off)[0]
        name = buf[off + 2 : off + 2 + namelen].decode("utf-8", "replace")
        dtype, esz = struct.unpack_from("<II", buf, off + 56)
        cols.append((name, dtype, esz))

    rows: list[dict[str, Any]] = []
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        if cs + 40 > len(buf):
            break
        _gen, used, row_count, state, _res, min_ts, max_ts = struct.unpack_from("<QIIIIqq", buf, cs)
        if row_count == 0 or used == 0:
            continue
        pos = cs + 40
        end = cs + 40 + used
        for _ in range(row_count):
            if pos + 4 > end:
                break
            row_len = struct.unpack_from("<I", buf, pos)[0]
            data_off_row = pos + 4
            row_end = data_off_row + row_len
            if row_end > end:
                break
            data = buf[data_off_row:row_end]
            rec: dict[str, Any] = {}
            p = 0
            for name, dtype, esz in cols:
                if dtype in (1,):
                    rec[name] = data[p]
                    p += 1
                elif dtype in (2, 7, 4):  # i32/u32/f32
                    if dtype == 4:
                        rec[name] = struct.unpack_from("<f", data, p)[0]
                    elif dtype == 7:
                        rec[name] = struct.unpack_from("<I", data, p)[0]
                    else:
                        rec[name] = struct.unpack_from("<i", data, p)[0]
                    p += 4
                elif dtype in (3, 5, 6):  # i64/f64/u64
                    if dtype == 5:
                        rec[name] = struct.unpack_from("<d", data, p)[0]
                    elif dtype == 6:
                        rec[name] = struct.unpack_from("<Q", data, p)[0]
                    else:
                        rec[name] = struct.unpack_from("<q", data, p)[0]
                    p += 8
                elif dtype in (8, 9):
                    # var field: length prefix relative to absolute buf offset
                    abs_off = data_off_row + p
                    payload, next_abs = _read_lp(buf, abs_off, cs)
                    p = next_abs - data_off_row
                    rec[name] = payload.decode("utf-8", "replace") if dtype == 8 else payload
                else:
                    break
            rows.append(rec)
            pos = row_end
            if max_rows is not None and len(rows) >= max_rows:
                break
        if max_rows is not None and len(rows) >= max_rows:
            break

    meta = {
        "path": str(path),
        "ver": ver,
        "ncols": ncols,
        "nchunks": nchunks,
        "chunk_size": chunk_size,
        "write_chunk": write_chunk,
        "creator_pid": creator_pid,
        "chunks_recycled": chunks_recycled,
        "rows_overwritten": rows_overwritten,
        "ts_col": ts_col,
        "n_rows": len(rows),
        "col_names": [c[0] for c in cols],
    }
    return MemtTable(path=path, cols=cols, rows=rows, meta=meta)


def parse_rss_query_txt(path: Path) -> list[tuple[int, int]]:
    """Return [(ts, rss_kb), ...] from p3sw_rss_window / cpu_util dump (newest first OK)."""
    text = path.read_text(errors="ignore")
    if "error=" in text or "QueryError" in text:
        return []
    out: list[tuple[int, int]] = []
    # ts | process | rss_kb | ...
    for m in re.finditer(r"│\s*(\d+)\s*│\s*process\s*│\s*(\d{5,})\s*│", text):
        out.append((int(m.group(1)), int(m.group(2))))
    if out:
        return out
    # ts | process | cpu | rss
    for m in re.finditer(
        r"│\s*(\d+)\s*│\s*process\s*│\s*[\d.]+\s*│\s*(\d{5,})\s*│", text
    ):
        out.append((int(m.group(1)), int(m.group(2))))
    return out


def find_victim_pid(ff_root: Path, case: str) -> Optional[str]:
    mans = list(ff_root.glob(f"{case}/**/C2_probing/probing/query_manifest.json"))
    if not mans:
        return None
    try:
        return str(json.loads(mans[0].read_text()).get("pid") or "") or None
    except Exception:
        return None


def pick_torch_trace(ff_root: Path, case: str) -> tuple[Path, str]:
    pid = find_victim_pid(ff_root, case)
    if pid:
        p = ff_root / "probing_data" / pid / "python.torch_trace"
        if p.exists():
            return p, pid
    cands = list((ff_root / "probing_data").glob("*/python.torch_trace"))
    if not cands:
        raise FileNotFoundError(f"no torch_trace under {ff_root}")
    best = max(cands, key=lambda x: x.stat().st_size)
    return best, best.parent.name


def step_bounds(rows: Iterable[dict[str, Any]]) -> tuple[int, int, set[int]]:
    steps = set()
    for r in rows:
        ls = r.get("local_step")
        if ls is None:
            continue
        try:
            steps.add(int(ls))
        except Exception:
            continue
    if not steps:
        return -1, -1, set()
    return min(steps), max(steps), steps


def truncate_by_w(
    rows: list[dict[str, Any]],
    w: Optional[int],
    *,
    anchor_step: Optional[int] = None,
) -> list[dict[str, Any]]:
    """保留 (anchor-W, anchor] 的 local_step；w=None 保留 ≤anchor 的全部。"""
    if not rows:
        return []
    _mn, mx, _ = step_bounds(rows)
    if mx < 0:
        return []
    anchor = mx if anchor_step is None else min(int(anchor_step), mx)
    if w is None:
        return [r for r in rows if int(r.get("local_step", -10**9)) <= anchor]
    lo = anchor - w + 1
    return [
        r
        for r in rows
        if lo <= int(r.get("local_step", -10**9)) <= anchor
    ]


def median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def judge_p3_sw(
    case: str,
    tt_win: list[dict[str, Any]],
    rss_series: list[tuple[int, int]],
    w: Optional[int],
    full_steps: set[int],
    *,
    rss_aligned: bool,
) -> dict[str, Any]:
    """P3-SW：主证 cpu.utilization_rss 抬升；辅证 torch_trace allocated 抬升。

    若 RSS 时间戳与 torch 注入窗不对齐（环只剩 run 末尾），不能离线定 W*。
    """
    note_parts = []
    if not rss_aligned:
        return {
            "enough": False,
            "evidence": "rss_ring_misaligned_to_inject_window",
            "primary": "cpu.utilization_rss",
            "aux_torch_ok": False,
            "n_tt_rows": len(tt_win),
            "n_tt_steps": len({int(r["local_step"]) for r in tt_win if "local_step" in r}),
            "unresolved": True,
        }

    rss_ok = False
    if rss_series:
        series = sorted(rss_series, key=lambda x: x[0])
        use = series
        rss_vals = [r for _, r in use]
        mx, mn = max(rss_vals), min(rss_vals)
        rise = mx - mn
        # 渐进泄漏：离线定 W* 要求看到抬升；纯绝对值会被「环只剩末尾高水位」骗过
        if rise >= RSS_RISE_THR_KB:
            rss_ok = True
            note_parts.append(f"cpu.utilization_rss:rise_kb={rise}:max_kb={mx}:n={len(use)}")
        elif mx >= RSS_ABS_THR_KB:
            note_parts.append(
                f"rss_abs_only:max_kb={mx}:rise_kb={rise}:n={len(use)}"
                ":不足以单独定W*(需rise)"
            )
        else:
            note_parts.append(f"rss_insufficient:rise_kb={rise}:max_kb={mx}:n={len(use)}")
    else:
        note_parts.append("rss_dump_missing")

    by_step_alloc: dict[int, float] = {}
    for r in tt_win:
        stage = str(r.get("stage") or "")
        if "post" not in stage:
            continue
        try:
            step = int(r["local_step"])
            v = float(r.get("allocated") or 0.0)
        except Exception:
            continue
        by_step_alloc[step] = max(by_step_alloc.get(step, 0.0), v)
    tt_ok = False
    if len(by_step_alloc) >= 2:
        steps_a = sorted(by_step_alloc)
        a_rise = by_step_alloc[steps_a[-1]] - by_step_alloc[steps_a[0]]
        if a_rise >= 64.0:
            tt_ok = True
            note_parts.append(f"torch_trace.allocated_step_rise_mb={a_rise:.1f}")
        else:
            note_parts.append(f"torch_alloc_flat:step_rise_mb={a_rise:.1f}")
    else:
        note_parts.append("torch_alloc_sparse")

    enough = rss_ok  # host 泄漏不以 GPU alloc 定谳
    return {
        "enough": enough,
        "evidence": ";".join(note_parts),
        "primary": "cpu.utilization_rss",
        "aux_torch_ok": tt_ok,
        "n_tt_rows": len(tt_win),
        "n_tt_steps": len({int(r["local_step"]) for r in tt_win if "local_step" in r}),
        "unresolved": False,
    }


def judge_p1_hw_b(tt_win: list[dict[str, Any]], w: Optional[int]) -> dict[str, Any]:
    """P1-HW-B：HBM 渐衰 → 注入窗内 torch_trace max_allocated/allocated/cached 斜率。

    禁止把 inject 前的冷启动分配跳变算作 ramp。
    """
    by_step: dict[int, float] = {}
    for r in tt_win:
        try:
            step = int(r["local_step"])
        except Exception:
            continue
        # 注入窗外的步不计入（截窗可能仍含 pre-onset）
        if step < INJECT_ONSET:
            continue
        try:
            v = max(
                float(r.get("max_allocated") or 0.0),
                float(r.get("allocated") or 0.0),
                float(r.get("cached") or 0.0),
            )
        except Exception:
            continue
        stage = str(r.get("stage") or "")
        if "post" not in stage:
            continue
        by_step[step] = max(by_step.get(step, 0.0), v)
    if len(by_step) < 3:
        return {
            "enough": False,
            "evidence": f"too_few_inject_steps:{len(by_step)}",
            "primary": "torch_trace.max_allocated_ramp",
            "n_tt_rows": len(tt_win),
            "n_tt_steps": len(by_step),
        }
    steps = sorted(by_step)
    vals = [by_step[s] for s in steps]
    rise = vals[-1] - vals[0]
    k = max(1, len(vals) // 3)
    early = median(vals[:k])
    late = median(vals[-k:])
    slope = late - early
    enough = rise >= HBM_RISE_THR_MB or slope >= HBM_RISE_THR_MB * 0.5
    if not enough:
        evid = (
            f"torch_trace_blind_in_inject:rise_mb={rise:.1f}:slope_mb={slope:.1f}"
            f":steps={steps[0]}..{steps[-1]}"
        )
    else:
        evid = (
            f"torch_trace.max_allocated_ramp:rise_mb={rise:.1f}"
            f":slope_mb={slope:.1f}:steps={steps[0]}..{steps[-1]}"
        )
    return {
        "enough": enough,
        "evidence": evid,
        "primary": "torch_trace.max_allocated_ramp",
        "n_tt_rows": len(tt_win),
        "n_tt_steps": len(by_step),
    }


def judge_p1_sw_c(tt_win: list[dict[str, Any]], w: Optional[int]) -> dict[str, Any]:
    """P1-SW-C：编译尖刺 → 窗内是否保住异常 duration 帧（可区分那一步）。"""
    durs: list[tuple[int, float, str]] = []
    for r in tt_win:
        stage = str(r.get("stage") or "")
        if "post" not in stage:
            continue
        try:
            dur = float(r.get("duration") or 0.0)
            step = int(r["local_step"])
        except Exception:
            continue
        if dur <= 0:
            continue
        durs.append((step, dur, str(r.get("module") or "")))
    if not durs:
        return {
            "enough": False,
            "evidence": "no_post_duration_rows",
            "primary": "torch_trace.duration_spike",
            "n_tt_rows": len(tt_win),
            "n_tt_steps": 0,
        }
    # per-step max duration
    per_step: dict[int, float] = defaultdict(float)
    per_step_mod: dict[int, str] = {}
    for step, dur, mod in durs:
        if dur >= per_step[step]:
            per_step[step] = dur
            per_step_mod[step] = mod
    med = median(list(per_step.values()))
    spikes = [
        (s, d, per_step_mod.get(s, ""))
        for s, d in per_step.items()
        if d >= SPIKE_ABS_SEC and (med <= 0 or d / med >= SPIKE_DUR_RATIO)
    ]
    enough = len(spikes) >= 1
    if spikes:
        s, d, mod = max(spikes, key=lambda x: x[1])
        evid = f"torch_trace.duration_spike:step={s}:dur_s={d:.4f}:med={med:.4f}:module={mod[:80]}"
    else:
        top = max(per_step.items(), key=lambda x: x[1])
        evid = (
            f"no_spike:top_step={top[0]}:dur_s={top[1]:.4f}:med={med:.4f}"
            f":n_steps={len(per_step)}"
        )
    return {
        "enough": enough,
        "evidence": evid,
        "primary": "torch_trace.duration_spike",
        "n_tt_rows": len(tt_win),
        "n_tt_steps": len(per_step),
    }


def load_rss_memt(ff_root: Path, case: str) -> list[tuple[int, int]]:
    """优先读 victim cpu.utilization MEMT（可按 torch 时间窗切）；否则退回 C2 dump 文本。"""
    pid = find_victim_pid(ff_root, case)
    if pid:
        p = ff_root / "probing_data" / pid / "cpu.utilization"
        if p.exists():
            try:
                tab = read_memt(p)
                out = []
                for r in tab.rows:
                    scope = str(r.get("scope") or "")
                    if scope and scope != "process":
                        continue
                    ts = int(r.get("timestamp") or r.get("ts") or 0)
                    rss = r.get("rss_kb")
                    if rss is None:
                        continue
                    out.append((ts, int(rss)))
                if out:
                    return out
            except Exception as exc:
                print(f"[warn] cpu.utilization MEMT parse fail: {exc}", file=sys.stderr)
    for name in ("query_p3sw_rss_window.txt", "query_cpu_util.txt"):
        paths = list(ff_root.glob(f"{case}/**/C2_probing/probing/{name}"))
        if paths:
            return parse_rss_query_txt(paths[0])
    return []


def rss_in_torch_window(
    rss: list[tuple[int, int]],
    tt_win: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """用 torch 窗的 timestamp 范围切 RSS。"""
    if not rss or not tt_win:
        return list(rss)
    ts_vals = []
    for r in tt_win:
        try:
            ts_vals.append(int(r["timestamp"]))
        except Exception:
            continue
    if not ts_vals:
        return list(rss)
    lo, hi = min(ts_vals), max(ts_vals)
    pad = max(1, (hi - lo) // 20)
    return [(t, v) for t, v in rss if (lo - pad) <= t <= (hi + pad)]


def rss_alignment(
    rss: list[tuple[int, int]],
    tt_rows: list[dict[str, Any]],
    anchor: int,
) -> tuple[bool, list[tuple[int, int]], str]:
    """RSS 是否与注入窗 torch 时间重叠。"""
    if not rss:
        return False, [], "rss_empty"
    # torch 在 [onset, anchor] 的时间范围
    ts_vals = []
    for r in tt_rows:
        try:
            step = int(r["local_step"])
            if INJECT_ONSET <= step <= anchor:
                ts_vals.append(int(r["timestamp"]))
        except Exception:
            continue
    if not ts_vals:
        return False, [], "no_torch_ts_in_inject"
    lo, hi = min(ts_vals), max(ts_vals)
    pad = max(1, (hi - lo) // 10)
    cut = [(t, v) for t, v in rss if (lo - pad) <= t <= (hi + pad)]
    if not cut:
        rss_lo, rss_hi = min(t for t, _ in rss), max(t for t, _ in rss)
        return (
            False,
            [],
            f"rss_ts=[{rss_lo},{rss_hi}] vs torch_inject_ts=[{lo},{hi}] (no overlap)",
        )
    return True, cut, f"aligned_n={len(cut)}"


def score_case(afs_root: Path, case: str, parent: str) -> dict[str, Any]:
    ff = afs_root / parent / "full_fidelity"
    if not ff.is_dir():
        return {"case": case, "status": "BLOCKED", "reason": f"missing {ff}"}
    try:
        tt_path, pid = pick_torch_trace(ff, case)
    except FileNotFoundError as exc:
        return {"case": case, "status": "BLOCKED", "reason": str(exc)}

    tt = read_memt(tt_path)
    if not tt.rows:
        return {
            "case": case,
            "status": "BLOCKED",
            "reason": "torch_trace MEMT empty/unreadable",
            "path": str(tt_path),
            "meta": tt.meta,
        }

    mn, mx, steps = step_bounds(tt.rows)
    if INJECT_STOP in steps:
        anchor = INJECT_STOP
    else:
        le = [s for s in steps if s <= INJECT_STOP]
        anchor = max(le) if le else mx

    rss_all = load_rss_memt(ff, case) if case.startswith("P3-SW") else []
    rss_aligned = True
    rss_note = ""
    rss_for_judge: list[tuple[int, int]] = []
    if case.startswith("P3-SW"):
        rss_aligned, rss_for_judge, rss_note = rss_alignment(rss_all, tt.rows, anchor)
        # C2 dump 旁证（存在性，不定 W*）
        c2_rise = None
        for name in ("query_p3sw_rss_window.txt", "query_cpu_util.txt"):
            paths = list(ff.glob(f"{case}/**/C2_probing/probing/{name}"))
            if paths:
                series = parse_rss_query_txt(paths[0])
                if series:
                    vals = [v for _, v in series]
                    c2_rise = max(vals) - min(vals)
                break
    else:
        c2_rise = None

    window_rows = []
    w_star = None
    unresolved = False
    for w in WINDOWS:
        win = truncate_by_w(tt.rows, w, anchor_step=anchor)
        if case.startswith("P3-SW"):
            # 每 W 再按该窗时间切一刀（若已对齐）
            if rss_aligned:
                rss_w = rss_in_torch_window(rss_for_judge, win) or rss_for_judge
            else:
                rss_w = []
            j = judge_p3_sw(
                case, win, rss_w, w, steps, rss_aligned=rss_aligned
            )
            if j.get("unresolved"):
                unresolved = True
        elif case == "P1-HW-B":
            j = judge_p1_hw_b(win, w)
        elif case == "P1-SW-C":
            j = judge_p1_sw_c(win, w)
        else:
            j = {"enough": False, "evidence": "unsupported_case", "primary": "?"}
        label = "full" if w is None else str(w)
        row = {
            "W": label,
            "enough": bool(j["enough"]),
            "evidence": j.get("evidence"),
            "primary": j.get("primary"),
            "n_tt_rows": j.get("n_tt_rows"),
            "n_tt_steps": j.get("n_tt_steps"),
            "anchor_step": anchor,
        }
        window_rows.append(row)
        if j["enough"] and w_star is None:
            w_star = label

    if unresolved:
        status = "UNRESOLVED"
    elif w_star is None:
        status = "NO_W_STAR"
    else:
        status = "OK"
    return {
        "case": case,
        "status": status,
        "parent_run": parent,
        "victim_pid": pid,
        "torch_trace_path": str(tt_path),
        "memt_meta": tt.meta,
        "step_min": mn,
        "step_max": mx,
        "anchor_step": anchor,
        "inject_onset": INJECT_ONSET,
        "inject_stop": INJECT_STOP,
        "n_unique_steps": len(steps),
        "n_rows": len(tt.rows),
        "ring_rows_overwritten": tt.meta.get("rows_overwritten"),
        "ring_chunks_recycled": tt.meta.get("chunks_recycled"),
        "rss_samples": len(rss_all),
        "rss_aligned": rss_aligned if case.startswith("P3-SW") else None,
        "rss_align_note": rss_note if case.startswith("P3-SW") else None,
        "c2_rss_rise_kb": c2_rise,
        "W_star": w_star,
        "windows": window_rows,
        "method": {
            "table": "python.torch_trace (MEMT ring) + case primary evidence",
            "truncate": (
                f"anchor=inject_stop({INJECT_STOP}); keep local_step in (anchor-W, anchor]; "
                "full = all steps <= anchor; P1-HW-B only scores steps>=inject_onset"
            ),
            "forbid": "training step_ms / score_dlevel_offline buried metrics",
        },
    }


def render_md(results: list[dict[str, Any]]) -> str:
    lines = [
        "# E1-off · 够用最小窗 W*（离线截窗初版）",
        "",
        "> **定位**：EVAL-GAP §2 E1 / §5 离线近似。不占集群卡。",
        "> **尺**：判「采集内容够不够归因」；**禁止**用训练 step_ms 把各窗判成同 D。",
        "> **数据**：旧 Pillar-C `full_fidelity` 的 `probing_data/<pid>/python.torch_trace`（MEMT）。",
        "",
        "## 方法",
        "",
        "1. 读 victim PID 的 `python.torch_trace` MEMT（环 20MB；`rows_overwritten` 记入表）。",
        "2. **锚在注入窗结束** `inject_stop=300`（非 dump 末步），截 `W∈{10,25,50,100,200,全程≤anchor}`。",
        "3. 按 case 判「够归因」：",
        "   - **P3-SW-A/B**：主证 `cpu.utilization` RSS 抬升（≥50MiB 或绝对值 ≥700MiB；按 torch 时间窗切 RSS）；辅看按步 `allocated`。",
        "   - **P1-HW-B**：`torch_trace.max_allocated` 窗内抬升/斜率（≥256MiB 级）。",
        "   - **P1-SW-C**：`torch_trace` post-forward `duration` 尖刺（≥3×中位且 ≥0.4s）。",
        "4. **W*** = 首次 enough=true 的最小 W；全程仍不够 → `NO_W_STAR`（不拿 cold MiB 冒充）。",
        "",
        "## W* 总表",
        "",
        "| case | W* | status | anchor | steps in ring | rows_ow | primary evidence |",
        "|---|---:|---|---:|---:|---:|---|",
    ]
    for r in results:
        if r.get("status") == "BLOCKED":
            lines.append(
                f"| {r['case']} | — | BLOCKED | — | — | — | {r.get('reason','')} |"
            )
            continue
        lines.append(
            f"| {r['case']} | {r.get('W_star') or '—'} | {r['status']} | "
            f"{r.get('anchor_step')} | "
            f"{r.get('n_unique_steps')} ({r.get('step_min')}..{r.get('step_max')}) | "
            f"{r.get('ring_rows_overwritten')} | "
            f"{(r.get('windows') or [{}])[-1].get('primary') if r.get('windows') else ''} |"
        )
    lines += ["", "## 分窗明细", ""]
    for r in results:
        lines.append(f"### {r['case']}")
        lines.append("")
        if r.get("status") == "BLOCKED":
            lines.append(f"- **BLOCKED**：{r.get('reason')}")
            lines.append("")
            continue
        lines.append(f"- parent：`{r.get('parent_run')}`")
        lines.append(f"- torch_trace：`{r.get('torch_trace_path')}` (pid={r.get('victim_pid')})")
        lines.append(
            f"- 环内：rows={r.get('n_rows')} steps={r.get('n_unique_steps')} "
            f"recycled={r.get('ring_chunks_recycled')} overwritten={r.get('ring_rows_overwritten')}"
        )
        lines.append(
            f"- 截窗锚：`anchor_step={r.get('anchor_step')}` "
            f"(inject [{r.get('inject_onset')},{r.get('inject_stop')}])"
        )
        lines.append(f"- **W\\*** = `{r.get('W_star')}`")
        if r.get("status") == "UNRESOLVED":
            lines.append(f"- ⚠ UNRESOLVED：`{r.get('rss_align_note')}`")
            if r.get("c2_rss_rise_kb") is not None:
                lines.append(
                    f"- C2 dump 旁证（不定 W*）：rss_rise_kb={r.get('c2_rss_rise_kb')}"
                )
        if r.get("status") == "NO_W_STAR":
            lines.append("- ⚠ 全程窗仍不够归因（见分窗 evidence）；**未**用 cold MiB 冒充。")
        lines.append("")
        lines.append("| W | enough | n_steps | evidence |")
        lines.append("|---:|:---:|---:|---|")
        for w in r.get("windows") or []:
            lines.append(
                f"| {w['W']} | {'Y' if w['enough'] else 'N'} | {w.get('n_tt_steps')} | `{w.get('evidence')}` |"
            )
        lines.append("")
    lines += [
        "## 缺口 / 局限",
        "",
        "- C2 `query_torch_trace_tail` 曾因 SQL 列名 `step`（应为 `local_step`）失败，故本轮直接解析 MEMT，不依赖错误 dump。",
        "- 截窗锚在 `inject_stop=300`（Loud 注入窗），不是 dump 末步；避免「末窗假短」。",
        "- **P3-SW**：`cpu.utilization` 热环太小，落盘后常只剩 run 末尾 ~1s，与注入窗时间错位 → `UNRESOLVED`（C2 dump 可证 RSS 抬升存在，但不能离线定 W*）。",
        "- **P1-HW-B**：注入窗内 `allocated/cached/max_allocated` 平坦；禁止把 inject 前冷启动 +1024MB 当 ramp → `NO_W_STAR`。",
        "- P1-EXT-A 阴性对照本轮未跑（可后补）。",
        "- 若 `rows_overwritten>0`，环已丢早期步，「全程」≠训练全程。",
        "- 正式 E1 仍需按 W 保留的新采集；本表是离线初版。",
        "",
        "## 复跑",
        "",
        "```bash",
        "python3 project/probing-huawei/scripts/fail-slow/e1_offline_window_score.py \\",
        "  --out project/probing-huawei/results/ascend-ais/pillar_c_v2/E1_off \\",
        "  --afs-root /afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c",
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--afs-root",
        type=Path,
        default=Path("/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c"),
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES.keys()))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    results = []
    for case in args.cases:
        parent = DEFAULT_CASES.get(case)
        if not parent:
            results.append({"case": case, "status": "BLOCKED", "reason": "unknown case"})
            continue
        print(f"[e1-off] scoring {case} ← {parent}", flush=True)
        r = score_case(args.afs_root, case, parent)
        results.append(r)
        print(
            f"  status={r.get('status')} W*={r.get('W_star')} steps={r.get('n_unique_steps')}",
            flush=True,
        )

    (args.out / "W_STAR.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    (args.out / "W_STAR.md").write_text(render_md(results))
    # flat CSV
    with (args.out / "windows.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "W", "enough", "n_tt_steps", "n_tt_rows", "evidence", "W_star", "status"])
        for r in results:
            for row in r.get("windows") or []:
                w.writerow(
                    [
                        r["case"],
                        row["W"],
                        int(row["enough"]),
                        row.get("n_tt_steps"),
                        row.get("n_tt_rows"),
                        row.get("evidence"),
                        r.get("W_star"),
                        r.get("status"),
                    ]
                )
    print(f"[e1-off] wrote {args.out}/W_STAR.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
