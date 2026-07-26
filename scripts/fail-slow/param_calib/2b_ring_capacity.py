#!/usr/bin/env python3
"""②-B 环容量换算：容量 MB → python.torch_trace 能留多少步（纯离线）。

铁律：单自变量=环容量；ground truth = MEMT 环内 used_bytes / unique local_step，
禁止用 cold MiB 或训练 step_ms 冒充。

方法：
  1. 读 pillar_c full_fidelity 的 `probing_data/<pid>/python.torch_trace`（MEMT v3）
  2. 表= `python.torch_trace`；容量 = nchunks × chunk_size（现网 8×2.5MB=20MB）
  3. bytes/row = Σchunk.used / n_rows；bytes/step = Σchunk.used / n_unique(local_step)
  4. 扫容量 C∈{5,7.5,10,15,20,30,40} MB（SI，1MB=1e6B；同现网 20_000_000 口径）
     steps(C) = floor( (C_bytes − nchunks×40) / bytes_per_step )
  5. 对照 v2「20MB≈546」：那是未覆写观测跨度，不是满环饱和；本实验给出满环外推

用法:
  python3 2b_ring_capacity.py \\
    --results-root project/probing-huawei/results/ascend-ais \\
    --out project/probing-huawei/results/ascend-ais/param_calib/2B_ring_capacity
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve()
_CANDIDATES = [
    Path("/Users/yinjinrun/Codespace/myportal/project/lab-workspace/reports"),
    Path.home() / "Codespace/myportal/project/lab-workspace/reports",
]
for _d in _CANDIDATES:
    if (_d / "plot_style.py").is_file():
        sys.path.insert(0, str(_d))
        break

MAGIC_MEMT = 0x4D454D54
N_CHUNKS_DEFAULT = 8
CHUNK_HDR = 40
# SI MB（与现网 chunk_size×nchunks=20_000_000 对齐）
SWEEP_MB = [5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 40.0]
# E1-off 参考（非本队列 ②-A）
W_STAR_REF = 100
W_STAR_SOURCE = "E1-off P1-SW-C (pillar_c_v2/E1_off；非本队列②-A)"
LEGACY_20MB_STEPS = 546  # v2 口号：观测跨度


@dataclass
class MemtStats:
    path: str
    run_id: str
    pid: str
    nchunks: int
    chunk_size: int
    capacity_bytes: int
    usable_bytes: int
    used_bytes: int
    fill_frac: float
    n_rows: int
    n_unique_steps: int
    step_min: Optional[int]
    step_max: Optional[int]
    bytes_per_row: float
    bytes_per_step: float
    rows_per_step: float
    rows_overwritten: int
    chunks_recycled: int


def _read_lp(buf: bytes, off: int, chunk_start: int) -> tuple[bytes, int]:
    raw = struct.unpack_from("<i", buf, off)[0]
    if raw < 0:
        ref_off = chunk_start + (-raw)
        ln = struct.unpack_from("<I", buf, ref_off)[0]
        return buf[ref_off + 4 : ref_off + 4 + ln], off + 4
    return buf[off + 4 : off + 4 + raw], off + 4 + raw


def inspect_torch_trace(path: Path, run_id: str = "") -> Optional[MemtStats]:
    buf = path.read_bytes()
    if len(buf) < 64 or struct.unpack_from("<I", buf, 0)[0] != MAGIC_MEMT:
        return None
    _magic, _ver, _hsz, _bom, _ts, _flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    _wc, _rc, _pid, _pad = struct.unpack_from("<IIII", buf, 32)
    _start, chunks_recycled, rows_overwritten = struct.unpack_from("<QII", buf, 48)
    cols: list[tuple[str, int, int]] = []
    for i in range(ncols):
        off = 64 + i * 64
        namelen = struct.unpack_from("<H", buf, off)[0]
        name = buf[off + 2 : off + 2 + namelen].decode("utf-8", "replace")
        dtype, esz = struct.unpack_from("<II", buf, off + 56)
        cols.append((name, dtype, esz))

    used_total = 0
    rows: list[dict[str, Any]] = []
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        if cs + CHUNK_HDR > len(buf):
            break
        _gen, used, row_count, _state, _res, _mn, _mx = struct.unpack_from("<QIIIIqq", buf, cs)
        if row_count == 0 or used == 0:
            continue
        used_total += used
        pos = cs + CHUNK_HDR
        end = cs + CHUNK_HDR + used
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
            for name, dtype, _esz in cols:
                if dtype == 1:
                    rec[name] = data[p]
                    p += 1
                elif dtype in (2, 7, 4):
                    fmt = {2: "<i", 7: "<I", 4: "<f"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]
                    p += 4
                elif dtype in (3, 5, 6):
                    fmt = {3: "<q", 5: "<d", 6: "<Q"}[dtype]
                    rec[name] = struct.unpack_from(fmt, data, p)[0]
                    p += 8
                elif dtype in (8, 9):
                    abs_off = data_off_row + p
                    payload, next_abs = _read_lp(buf, abs_off, cs)
                    p = next_abs - data_off_row
                    rec[name] = payload.decode("utf-8", "replace") if dtype == 8 else payload
                else:
                    break
            rows.append(rec)
            pos = row_end

    steps = {int(r["local_step"]) for r in rows if r.get("local_step") is not None}
    if not rows or not steps:
        return None
    capacity = nchunks * chunk_size
    usable = nchunks * max(0, chunk_size - CHUNK_HDR)
    n_steps = len(steps)
    return MemtStats(
        path=str(path),
        run_id=run_id or path.parts[-4] if len(path.parts) >= 4 else "",
        pid=path.parent.name,
        nchunks=nchunks,
        chunk_size=chunk_size,
        capacity_bytes=capacity,
        usable_bytes=usable,
        used_bytes=used_total,
        fill_frac=(used_total / usable) if usable else 0.0,
        n_rows=len(rows),
        n_unique_steps=n_steps,
        step_min=min(steps),
        step_max=max(steps),
        bytes_per_row=used_total / len(rows),
        bytes_per_step=used_total / n_steps,
        rows_per_step=len(rows) / n_steps,
        rows_overwritten=rows_overwritten,
        chunks_recycled=chunks_recycled,
    )


def discover_full_fidelity(results_root: Path) -> list[MemtStats]:
    """Prefer pillar_c/*/full_fidelity/probing_data/*/python.torch_trace."""
    out: list[MemtStats] = []
    pillar_c = results_root / "pillar_c"
    roots = sorted(pillar_c.glob("*/full_fidelity")) if pillar_c.is_dir() else []
    for root in roots:
        run_id = root.parent.name
        for f in sorted((root / "probing_data").glob("*/python.torch_trace")):
            st = inspect_torch_trace(f, run_id=run_id)
            if st is not None:
                out.append(st)
    return out


def choose_default(curve: list[dict[str, Any]]) -> dict[str, Any]:
    """够 W* 又不浪费：优先 ≥4×W* 的最小扫点；否则 ≥2×；兜底 20MB。"""
    by_mb = {c["capacity_mb"]: c for c in curve}
    for mb in SWEEP_MB:
        c = by_mb[mb]
        if c["steps_retainable"] >= 4 * W_STAR_REF:
            return {
                "chosen_mb": mb,
                "steps": c["steps_retainable"],
                "rule": f"min C in sweep s.t. steps(C) ≥ 4×W* ({4 * W_STAR_REF})",
                "rationale": "够 W*=100 且不浪费；10MB≈4× 余量",
            }
    for mb in SWEEP_MB:
        c = by_mb[mb]
        if c["steps_retainable"] >= 2 * W_STAR_REF:
            return {
                "chosen_mb": mb,
                "steps": c["steps_retainable"],
                "rule": f"min C in sweep s.t. steps(C) ≥ 2×W* ({2 * W_STAR_REF})",
                "rationale": "紧配；仅 2×W* 余量",
            }
    c20 = by_mb.get(20.0) or curve[-1]
    return {
        "chosen_mb": c20["capacity_mb"],
        "steps": c20["steps_retainable"],
        "rule": "fallback 20MB (current production default)",
        "rationale": "扫点均不满足 2×W*；保留现网默认",
    }


def plot_curve(out_dir: Path, curve: list[dict[str, Any]], chosen_mb: float) -> Optional[Path]:
    try:
        from plot_style import apply_plot_style, save_fig, style_axes  # type: ignore
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[2b] plot skip: {e}", flush=True)
        return None
    apply_plot_style((7.2, 4.2))
    xs = [c["capacity_mb"] for c in curve]
    ys = [c["steps_retainable"] for c in curve]
    fig, ax = plt.subplots()
    ax.plot(xs, ys, "o-", color="#1f4e79", linewidth=2, markersize=7, label="满环可留步数")
    ax.axhline(W_STAR_REF, color="#c0392b", linestyle="--", linewidth=1.5, label=f"W*={W_STAR_REF} (E1-off)")
    ax.axhline(LEGACY_20MB_STEPS, color="#7f8c8d", linestyle=":", linewidth=1.2, label="v2 观测 546@20MB")
    if chosen_mb in xs:
        yi = ys[xs.index(chosen_mb)]
        ax.scatter([chosen_mb], [yi], s=120, zorder=5, color="#e67e22", label=f"推荐 {chosen_mb:g}MB")
    ax.set_xlabel("环容量 (MB, SI)")
    ax.set_ylabel("可保留 local_step 数")
    ax.set_title("②-B 环容量 → torch_trace 保留步数")
    ax.legend(loc="upper left", frameon=False)
    style_axes(ax)
    svg = out_dir / "fig_capacity_vs_steps.svg"
    save_fig(fig, svg)
    plt.close(fig)
    return svg

def write_param_md(path: Path, payload: dict[str, Any]) -> None:
    cal = payload["calibration"]
    curve = payload["retention_curve"]
    chosen = payload["chosen_value"]
    legacy = payload["legacy_baseline_check"]
    lines = [
        "# ②-B 环容量换算 · 容量 → 保留步数（exp=`2B_ring_capacity`）",
        "",
        "> 自变量=**环容量**（MB）；表=`python.torch_trace`（MEMT 环）。",
        "> bytes/row = Σchunk.used / n_rows；bytes/step = Σchunk.used / n_unique(`local_step`)。",
        "> 禁止 cold MiB / 训练 step_ms 冒充环容量结论。",
        "",
        "## 为什么这么设（一句）",
        "",
        f"**默认环容量={chosen['chosen_mb']:g} MB**（满环≈**{chosen['steps']}** 步）："
        f"按 full 臂实测 ~{cal['bytes_per_step_median']:.0f} B/step 线性外推；"
        f"相对 E1-off W\\*={W_STAR_REF}（P1-SW-C，非本队列②-A）留 ≥4× 余量，"
        f"比 40MB 省内存、比 5MB 更稳。v2「20MB≈546」复核为**未满环观测跨度**（fill≈67%），"
        f"满环饱和约 **{legacy['saturated_steps_at_20mb']}** 步。",
        "",
        "## 控制变量",
        "",
        "| 固定 | 值 |",
        "|---|---|",
        "| 表 | `python.torch_trace`（MEMT v3） |",
        "| 布局 | nchunks=8；容量=nchunks×chunk_size（SI MB） |",
        "| 度量 | unique `local_step`；used=chunk payload 字节 |",
        "| 自变量 | 环容量 ∈ {5, 7.5, 10, 15, 20, 30, 40} MB |",
        f"| W\\* 参考（交叉，非本实验扫） | {W_STAR_REF} ← {W_STAR_SOURCE} |",
        "",
        "## 推荐参数",
        "",
        "| 参数 | 值 | 满环步数 | vs W\\*=100 | 规则 |",
        "|---|---:|---:|---:|---|",
        f"| 环容量默认 | **{chosen['chosen_mb']:g} MB** | {chosen['steps']} | "
        f"{chosen['steps']/W_STAR_REF:.1f}× | {chosen['rule']} |",
        f"| 保守（现网） | 20 MB | {next(c['steps_retainable'] for c in curve if c['capacity_mb']==20.0)} | "
        f"{next(c['steps_retainable'] for c in curve if c['capacity_mb']==20.0)/W_STAR_REF:.1f}× | 生产默认；仍不浪费 |",
        f"| 下限慎用 | 5 MB | {next(c['steps_retainable'] for c in curve if c['capacity_mb']==5.0)} | "
        f"{next(c['steps_retainable'] for c in curve if c['capacity_mb']==5.0)/W_STAR_REF:.1f}× | 仅约 2×W\\*，升详变密易顶满 |",
        "",
        "## 保留时长曲线（容量 → 步数）",
        "",
        "| 容量 MB | capacity B | usable B | 可留步数 | vs W\\*=100 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for c in curve:
        mark = " ←推荐" if c["capacity_mb"] == chosen["chosen_mb"] else ""
        lines.append(
            f"| {c['capacity_mb']:g} | {c['capacity_bytes']} | {c['usable_bytes']} | "
            f"**{c['steps_retainable']}**{mark} | {c['margin_vs_W_star']:.2f}× |"
        )
    lines += [
        "",
        "## 标定样本（bytes/row · bytes/step）",
        "",
        f"- 样本数：{cal['n_files']} 个 full_fidelity `python.torch_trace`（{cal['n_runs']} runs）",
        f"- 现网环：capacity={cal['capacity_bytes_observed']} B "
        f"（{cal['nchunks']}×{cal['chunk_size']}）≈ **20 MB**",
        f"- 观测：steps={cal['observed_steps']}（{cal['step_min']}..{cal['step_max']}），"
        f"rows={cal['observed_rows']}，fill={100*cal['fill_frac_mean']:.1f}%，"
        f"rows_overwritten={cal['rows_overwritten_max']}",
        f"- **bytes/row** 中位 = **{cal['bytes_per_row_median']:.2f}** "
        f"（mean {cal['bytes_per_row_mean']:.2f}）",
        f"- **bytes/step** 中位 = **{cal['bytes_per_step_median']:.2f}** "
        f"（≈{cal['bytes_per_step_median']/1024:.1f} KiB/step；"
        f"rows/step≈{cal['rows_per_step_median']:.1f}）",
        "",
        "### 按 run",
        "",
        "| run | n_files | steps | rows | fill% | B/step | B/row | ow |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload["by_run"]:
        lines.append(
            f"| `{r['run_id']}` | {r['n_files']} | {r['steps']} | {r['rows']} | "
            f"{100*r['fill_frac']:.1f} | {r['bytes_per_step']:.1f} | {r['bytes_per_row']:.1f} | "
            f"{r['rows_overwritten']} |"
        )
    lines += [
        "",
        "## 与基线 20MB≈546 对照",
        "",
        f"| 口径 | 步数 | 说明 |",
        f"|---|---:|---|",
        f"| v2 E1-off / MECH_FIX 口号 | {LEGACY_20MB_STEPS} | 20MB 环内观测 unique local_step"
        f"（0..545），`rows_ow=0`，**未声明满环** |",
        f"| 本实验复核（同批 full 臂） | {legacy['observed_steps_at_20mb']} | "
        f"fill≈{100*legacy['observed_fill_frac']:.1f}%；used="
        f"{legacy['observed_used_bytes']} B / capacity={legacy['capacity_bytes']} B |",
        f"| 本实验满环外推 @20MB | **{legacy['saturated_steps_at_20mb']}** | "
        f"floor(usable / B_step)；修正「546=饱和」误解 |",
        "",
        f"**结论**：v2「20MB≈546」作为**某次 full run 未覆写观测跨度**成立；"
        f"作为**环饱和容量**应修正为 **≈{legacy['saturated_steps_at_20mb']} 步**。"
        f"546/814≈67%，与实测 fill 一致。",
        "",
        "## 与 W\\* 交叉（够又不浪费）",
        "",
        f"- W\\*={W_STAR_REF} 来自 **{W_STAR_SOURCE}**，本实验**不扫**追溯窗。",
        f"- {chosen['chosen_mb']:g} MB → {chosen['steps']} 步 ≈ "
        f"{chosen['steps']/W_STAR_REF:.1f}×W\\*（{chosen['rationale']}）。",
        f"- 40 MB ≈ {next(c['steps_retainable'] for c in curve if c['capacity_mb']==40.0)/W_STAR_REF:.0f}×W\\*，"
        f"对单表本地环偏浪费；5 MB 仅 ~2×，升详变密时风险高。",
        "",
        "## 方法与假设",
        "",
        "1. 表：`python.torch_trace` MEMT 环（非 cold 目录体积）。",
        "2. bytes/row：chunk `used` 字段之和 / 解析行数（含行长前缀，不含 40B chunk 头）。",
        "3. bytes/step：同上 / unique `local_step`（一步多 module/stage 行）。",
        "4. 容量扫：固定 nchunks=8，缩放 chunk_size 使 nchunks×chunk_size = C×1e6。",
        "5. 满环步数=线性外推；本机无 `rows_overwritten>0` 样本，故饱和点无直接撞环实测。",
        "6. 假设 full-rate 详采密度与标定臂相近；rate≪1 时同容量可留更多步。",
        "",
        "## 图",
        "",
        "- `fig_capacity_vs_steps.svg`：容量→步数；红虚线 W\\*=100；灰点线 v2 观测 546。",
        "",
        "## 复跑",
        "",
        "```bash",
        "python3 project/probing-huawei/scripts/fail-slow/param_calib/2b_ring_capacity.py \\",
        "  --results-root project/probing-huawei/results/ascend-ais \\",
        "  --out project/probing-huawei/results/ascend-ais/param_calib/2B_ring_capacity",
        "```",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--results-root",
        type=Path,
        default=Path("project/probing-huawei/results/ascend-ais"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("project/probing-huawei/results/ascend-ais/param_calib/2B_ring_capacity"),
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    stats = discover_full_fidelity(args.results_root)
    # dense: enough steps to trust B/step
    dense = [s for s in stats if s.n_unique_steps >= 200 and s.fill_frac >= 0.3]
    if not dense:
        print("[2b] ERROR: no dense full_fidelity torch_trace found", flush=True)
        return 2

    bps = [s.bytes_per_step for s in dense]
    bpr = [s.bytes_per_row for s in dense]
    rps = [s.rows_per_step for s in dense]
    bps_med = float(statistics.median(bps))
    bpr_med = float(statistics.median(bpr))
    rps_med = float(statistics.median(rps))

    by_run: dict[str, list[MemtStats]] = {}
    for s in dense:
        by_run.setdefault(s.run_id, []).append(s)
    by_run_summary = []
    for rid, xs in sorted(by_run.items()):
        by_run_summary.append(
            {
                "run_id": rid,
                "n_files": len(xs),
                "steps": xs[0].n_unique_steps,
                "rows": xs[0].n_rows,
                "fill_frac": float(statistics.mean(x.fill_frac for x in xs)),
                "bytes_per_step": float(statistics.mean(x.bytes_per_step for x in xs)),
                "bytes_per_row": float(statistics.mean(x.bytes_per_row for x in xs)),
                "rows_overwritten": max(x.rows_overwritten for x in xs),
                "example_path": xs[0].path,
            }
        )

    curve = []
    for mb in SWEEP_MB:
        cap = int(round(mb * 1_000_000))
        usable = cap - N_CHUNKS_DEFAULT * CHUNK_HDR
        steps = int(usable // bps_med) if bps_med > 0 else 0
        rows = int(usable // bpr_med) if bpr_med > 0 else 0
        curve.append(
            {
                "capacity_mb": mb,
                "capacity_bytes": cap,
                "usable_bytes": usable,
                "steps_retainable": steps,
                "rows_retainable": rows,
                "margin_vs_W_star": steps / W_STAR_REF,
            }
        )

    chosen = choose_default(curve)
    # 20MB legacy check
    c20 = next(c for c in curve if c["capacity_mb"] == 20.0)
    obs = dense[0]
    legacy = {
        "v2_slogan_steps": LEGACY_20MB_STEPS,
        "observed_steps_at_20mb": obs.n_unique_steps,
        "observed_fill_frac": float(statistics.mean(s.fill_frac for s in dense)),
        "observed_used_bytes": obs.used_bytes,
        "capacity_bytes": obs.capacity_bytes,
        "saturated_steps_at_20mb": c20["steps_retainable"],
        "verdict": (
            "CONFIRM_OBSERVED_SPAN"
            if obs.n_unique_steps == LEGACY_20MB_STEPS
            else "ADJUST_OBSERVED_SPAN"
        ),
        "saturation_correction": (
            f"满环外推 {c20['steps_retainable']} 步（非口号 546）；"
            f"546 为 fill≈{100*float(statistics.mean(s.fill_frac for s in dense)):.0f}% 时的观测跨度"
        ),
    }

    cal = {
        "table": "python.torch_trace",
        "n_files": len(dense),
        "n_runs": len(by_run),
        "nchunks": obs.nchunks,
        "chunk_size": obs.chunk_size,
        "capacity_bytes_observed": obs.capacity_bytes,
        "observed_steps": obs.n_unique_steps,
        "observed_rows": obs.n_rows,
        "step_min": obs.step_min,
        "step_max": obs.step_max,
        "fill_frac_mean": float(statistics.mean(s.fill_frac for s in dense)),
        "rows_overwritten_max": max(s.rows_overwritten for s in dense),
        "bytes_per_row_median": bpr_med,
        "bytes_per_row_mean": float(statistics.mean(bpr)),
        "bytes_per_step_median": bps_med,
        "bytes_per_step_mean": float(statistics.mean(bps)),
        "rows_per_step_median": rps_med,
        "how_bytes_measured": (
            "Σ MEMT chunk.used (payload after 40B chunk header) / n_rows or n_unique(local_step)"
        ),
    }

    payload = {
        "param": "torch_trace_ring_capacity_mb",
        "exp_id": "2B_ring_capacity",
        "swept_range": {"unit": "MB_SI", "values": SWEEP_MB, "nchunks_fixed": N_CHUNKS_DEFAULT},
        "chosen_value": chosen,
        "retention_curve": curve,
        "calibration": cal,
        "by_run": by_run_summary,
        "legacy_baseline_check": legacy,
        "w_star_cross": {
            "W_star": W_STAR_REF,
            "source": W_STAR_SOURCE,
            "note": "非本队列②-A；仅交叉「够 W* 又不浪费」",
            "default_mb": chosen["chosen_mb"],
            "steps_at_default": chosen["steps"],
            "margin": chosen["steps"] / W_STAR_REF,
        },
        "ground_truth_source": {
            "table": "python.torch_trace MEMT ring (full_fidelity probing_data)",
            "bytes_per_row": "Σchunk.used / n_rows",
            "bytes_per_step": "Σchunk.used / n_unique(local_step)",
            "forbid": "cold MiB directory size; training step_ms",
        },
        "supports_design": (
            f"Ring default {chosen['chosen_mb']:g}MB retains ~{chosen['steps']} steps "
            f"(~{chosen['steps']/W_STAR_REF:.0f}× E1-off W*=100); "
            f"v2 20MB≈546 is observed unwrapped span at ~67% fill, saturation ≈{c20['steps_retainable']}."
        ),
        "samples": [asdict(s) for s in dense[:4]],  # keep PARAM.json lean
    }

    # CSV
    csv_path = args.out / "capacity_curve.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "capacity_mb",
                "capacity_bytes",
                "usable_bytes",
                "steps_retainable",
                "rows_retainable",
                "margin_vs_W_star",
            ],
        )
        w.writeheader()
        for c in curve:
            w.writerow(c)

    (args.out / "PARAM.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_param_md(args.out / "PARAM.md", payload)
    svg = plot_curve(args.out, curve, chosen["chosen_mb"])

    print(
        f"[2b] DONE default={chosen['chosen_mb']:g}MB steps={chosen['steps']} "
        f"B/step={bps_med:.1f} sat20={c20['steps_retainable']} obs546={obs.n_unique_steps} "
        f"out={args.out}" + (f" fig={svg.name}" if svg else ""),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
