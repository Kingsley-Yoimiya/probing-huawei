#!/usr/bin/env python3
"""①-A 档阈 d1_min_ratio：扫 θ → C0 定 FPR、C1 定召回（纯离线）。

铁律：单自变量=θ；固定窗[100,300]、victim 战役约定。
- 召回：C1 窗中位 step_ms / C0 同窗（与 accept_loud 同尺，rank0 全局步）
- FPR：C0 健康线稳态滑动窗 / 本 run 稳态中位（无注入触发=假阳性）
- θ*：每档 FPR 预算 B 下「FPR 刚压到 B 的最低 θ」（方案原文）；弱档 B 更大

用法:
  python3 1a_dose_threshold.py \\
    --results-root project/probing-huawei/results/ascend-ais \\
    --out project/probing-huawei/results/ascend-ais/param_calib/1A_dose_threshold
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# plot_style：优先 myportal 挂载，其次同级 Codespace
_HERE = Path(__file__).resolve()
_CANDIDATES = [
    _HERE.parents[5] / "Codespace/myportal/project/lab-workspace/reports",  # via probing-huawei abs
    Path("/Users/yinjinrun/Codespace/myportal/project/lab-workspace/reports"),
    Path.home() / "Codespace/myportal/project/lab-workspace/reports",
]
for _d in _CANDIDATES:
    if (_d / "plot_style.py").is_file():
        sys.path.insert(0, str(_d))
        break

WINDOW_LO = 100
WINDOW_HI = 300
WINDOW_LEN = WINDOW_HI - WINDOW_LO + 1  # 201
VICTIM = 7
GATE_RANK = 0
THETA_LO = 1.02
THETA_HI = 1.50
THETA_STEP = 0.02
STEADY_LO = 50
DOSE_FPR_BUDGET = {
    "loud": 0.01,  # 强档：健康误报 ≤1%
    "quiet": 0.05,  # 中档：~5% 换灵敏度
    "masked": 0.12,  # 弱档：必须更敏感
}
LEGACY = {"loud": 1.30, "quiet": 1.15, "masked": 1.05}

GOLDEN = {
    "loud": [
        ("P3-EXT-A", "20260725_001251-yjr-as-c-p3-ext-a-loud"),
        ("P3-SW-C", "20260725_135238-yjr-as-c-p3-sw-c-loud"),
    ],
    "quiet": [
        ("P3-EXT-A", "20260726_075912-yjr-as-c-p3-ext-a-quiet"),
        ("P3-SW-C", "20260726_125953-yjr-as-c-p3-sw-c-quiet"),
    ],
    "masked": [
        ("P3-EXT-A", "20260726_094648-yjr-as-c-p3-ext-a-masked"),
        ("P3-SW-C", "20260726_135016-yjr-as-c-p3-sw-c-masked"),
    ],
}

LOUD_EXPAND = [
    ("P1-EXT-A", "20260725_011129-yjr-as-c-p1-ext-a-loud"),
    ("P3-SW-A", "20260725_012957-yjr-as-c-p3-sw-a-loud"),
    ("P1-EXT-B", "20260725_014350-yjr-as-c-p1-ext-b-loud"),
    ("P3-EXT-B", "20260725_020212-yjr-as-c-p3-ext-b-loud"),
    ("P3-EXT-C", "20260725_021906-yjr-as-c-p3-ext-c-loud"),
    ("P1-SW-A", "20260725_114556-yjr-as-c-p1-sw-a-loud"),
    ("P1-SW-B", "20260725_115732-yjr-as-c-p1-sw-b-loud"),
    ("P1-SW-C", "20260725_121105-yjr-as-c-p1-sw-c-loud"),
    ("P2-SW-B", "20260725_122911-yjr-as-c-p2-sw-b-loud"),
    ("P2-SW-C", "20260725_124102-yjr-as-c-p2-sw-c-loud"),
    ("P3-SW-B", "20260725_125558-yjr-as-c-p3-sw-b-loud"),
    ("P1-HW-B", "20260725_142359-yjr-as-c-p1-hw-b-loud"),
]


@dataclass
class CaseRatio:
    case: str
    dose: str
    run_id: str
    golden: bool
    c0_med: float
    c1_med: float
    ratio: float
    c0_med_v7: float | None
    c1_med_v7: float | None
    ratio_v7: float | None


def find_rank(run_root: Path, cfg_glob: str, rank: int) -> Path | None:
    hits = sorted(
        run_root.glob(f"**/by_pod/*/round_1/{cfg_glob}/ranks/rank_{rank:04d}.jsonl")
    )
    if hits:
        return hits[0]
    if cfg_glob.startswith("C1"):
        hits = sorted(run_root.glob(f"**/C1_*/ranks/rank_{rank:04d}.jsonl"))
        return hits[0] if hits else None
    hits = sorted(run_root.glob(f"**/{cfg_glob}/ranks/rank_{rank:04d}.jsonl"))
    return hits[0] if hits else None


def load_step_ms(path: Path) -> dict[int, float]:
    out: dict[int, float] = {}
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "step_ms" not in o or "step" not in o:
                continue
            out[int(o["step"])] = float(o["step_ms"])
    return out


def median_of(series: dict[int, float], lo: int, hi: int) -> float | None:
    xs = [series[s] for s in series if lo <= s <= hi]
    return float(statistics.median(xs)) if xs else None


def thetas(lo: float = THETA_LO, hi: float = THETA_HI, step: float = THETA_STEP) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


def sliding_self_ratios(
    series: dict[int, float],
    win_len: int = WINDOW_LEN,
    stride: int = 5,
    steady_lo: int = STEADY_LO,
) -> list[float]:
    """C0 自比：稳态区候选窗中位 / 全稳态中位。"""
    steps = sorted(s for s in series if s >= steady_lo)
    if len(steps) < win_len + 10:
        return []
    vals = [series[s] for s in steps]
    gmed = statistics.median(vals)
    if gmed <= 0:
        return []
    return [
        statistics.median(vals[i : i + win_len]) / gmed
        for i in range(0, len(vals) - win_len + 1, stride)
    ]


def load_case_ratio(
    results_root: Path, case: str, dose: str, run_id: str, golden: bool
) -> CaseRatio | None:
    run_root = results_root / run_id
    if not run_root.exists():
        return None
    c0p = find_rank(run_root, "C0_baseline", GATE_RANK)
    c1p = find_rank(run_root, "C1_*", GATE_RANK)
    if not c0p or not c1p:
        return None
    c0 = load_step_ms(c0p)
    c1 = load_step_ms(c1p)
    c0_med = median_of(c0, WINDOW_LO, WINDOW_HI)
    c1_med = median_of(c1, WINDOW_LO, WINDOW_HI)
    if c0_med is None or c1_med is None or c0_med <= 0:
        return None
    c0v = c1v = r7 = None
    c0p7 = find_rank(run_root, "C0_baseline", VICTIM)
    c1p7 = find_rank(run_root, "C1_*", VICTIM)
    if c0p7 and c1p7:
        s0 = load_step_ms(c0p7)
        s1 = load_step_ms(c1p7)
        c0v = median_of(s0, WINDOW_LO, WINDOW_HI)
        c1v = median_of(s1, WINDOW_LO, WINDOW_HI)
        if c0v and c0v > 0 and c1v is not None:
            r7 = c1v / c0v
    return CaseRatio(
        case=case,
        dose=dose,
        run_id=run_id,
        golden=golden,
        c0_med=c0_med,
        c1_med=c1_med,
        ratio=c1_med / c0_med,
        c0_med_v7=c0v,
        c1_med_v7=c1v,
        ratio_v7=r7,
    )


def collect_c0_fpr_ratios(results_root: Path, run_ids: list[str]) -> list[float]:
    ratios: list[float] = []
    seen: set[str] = set()
    for rid in run_ids:
        if rid in seen:
            continue
        seen.add(rid)
        p = find_rank(results_root / rid, "C0_baseline", GATE_RANK)
        if not p:
            continue
        ratios.extend(sliding_self_ratios(load_step_ms(p)))
    return ratios


def recall_at(cases: list[CaseRatio], theta: float) -> float:
    if not cases:
        return float("nan")
    return sum(1 for c in cases if c.ratio >= theta) / len(cases)


def fpr_at(self_ratios: list[float], theta: float) -> float:
    if not self_ratios:
        return float("nan")
    return sum(1 for r in self_ratios if r >= theta) / len(self_ratios)


def choose_theta(sweep: list[dict], fpr_budget: float, dose: str) -> dict:
    """θ* = FPR 刚压到该档预算 B 的最低阈。"""
    feasible = [
        r for r in sweep if (not math.isnan(r["fpr"])) and r["fpr"] <= fpr_budget
    ]
    if feasible:
        best = min(feasible, key=lambda r: r["theta"])
        primary = {
            "theta_star": best["theta"],
            "recall": best["recall"],
            "fpr": best["fpr"],
            "fpr_budget": fpr_budget,
            "rule": (
                f"min θ s.t. FPR≤{fpr_budget:.0%} "
                f"(dose={dose}；方案「FPR 刚压到可接受的最低阈」)"
            ),
            "feasible": True,
        }
    else:
        best = min(sweep, key=lambda r: r["fpr"] if not math.isnan(r["fpr"]) else 9e9)
        primary = {
            "theta_star": best["theta"],
            "recall": best["recall"],
            "fpr": best["fpr"],
            "fpr_budget": fpr_budget,
            "rule": f"no θ with FPR≤{fpr_budget:.0%}; fallback min-FPR",
            "feasible": False,
        }

    full = [r for r in sweep if r["recall"] >= 0.999]
    theta_catch = max(full, key=lambda r: r["theta"])["theta"] if full else None
    youden = max(sweep, key=lambda r: (r["recall"] - r["fpr"], -r["theta"]))
    primary["theta_catch_full_recall"] = theta_catch
    primary["theta_youden"] = youden["theta"]
    primary["youden_J"] = youden["recall"] - youden["fpr"]
    primary["legacy"] = LEGACY[dose]
    return primary


def plot_curves(out_dir: Path, by_dose: dict) -> list[str]:
    try:
        import matplotlib.pyplot as plt
        from plot_style import apply_plot_style, save_fig, style_axes
    except Exception as e:
        print(f"[warn] plot skip: {e}", file=sys.stderr)
        return []

    paths: list[str] = []
    for dose, payload in by_dose.items():
        apply_plot_style((9, 5))
        fig, ax = plt.subplots()
        th = [r["theta"] for r in payload["sweep"]]
        ax.plot(
            th,
            [r["recall"] for r in payload["sweep"]],
            "o-",
            label="召回 (C1/C0≥θ)",
            color="tab:orange",
        )
        ax.plot(
            th,
            [r["fpr"] for r in payload["sweep"]],
            "s--",
            label="FPR (C0 自比)",
            color="tab:blue",
        )
        b = DOSE_FPR_BUDGET[dose]
        ax.axhline(b, color="tab:red", ls=":", lw=1.5, label=f"FPR 预算 {b:.0%}")
        ts = payload["chosen"]["theta_star"]
        ax.axvline(ts, color="tab:green", ls="-.", lw=1.5, label=f"θ*={ts}")
        ax.axvline(
            LEGACY[dose], color="tab:gray", ls=":", lw=1.2, label=f"旧默认 {LEGACY[dose]}"
        )
        ax.set_xlabel("判据阈 θ（d1_min_ratio）")
        ax.set_ylabel("比率")
        ax.set_title(f"①-A 档阈标定 · {dose}")
        ax.set_ylim(-0.05, 1.05)
        ax.legend(loc="best", fontsize=14)
        style_axes(ax)
        p = out_dir / f"fig_fpr_recall_{dose}.svg"
        save_fig(fig, p)
        paths.append(str(p))

    apply_plot_style((8, 4.5))
    fig, ax = plt.subplots()
    doses = list(by_dose.keys())
    xs = list(range(len(doses)))
    stars = [by_dose[d]["chosen"]["theta_star"] for d in doses]
    legs = [LEGACY[d] for d in doses]
    w = 0.35
    ax.bar(
        [x - w / 2 for x in xs],
        stars,
        w,
        label="θ*（数据）",
        color="tab:orange",
        hatch="//",
    )
    ax.bar(
        [x + w / 2 for x in xs],
        legs,
        w,
        label="旧默认",
        color="tab:gray",
        hatch="xx",
        alpha=0.7,
    )
    ax.set_xticks(xs)
    ax.set_xticklabels(doses)
    ax.set_ylabel("θ")
    ax.set_title("每档推荐 θ* vs 旧硬编码")
    ax.legend()
    style_axes(ax)
    p = out_dir / "fig_theta_star_by_dose.svg"
    save_fig(fig, p)
    paths.append(str(p))
    return paths


def write_param_md(out_dir: Path, result: dict, fig_paths: list[str]) -> None:
    lines: list[str] = [
        "# ①-A 档阈标定 · `d1_min_ratio`（exp=`1A_dose_threshold`）",
        "",
        "> 单自变量=θ；固定窗 `[100,300]`、闸门 rank0 `step_ms` 中位（与 `accept_loud` / D1 同尺）；"
        f"victim={VICTIM} 为战役真值旁证。",
        "> ground truth：C0→FPR，C1→召回。禁止 cold / 采集差异叙事。",
        "",
        "## 为什么这么设（一句）",
        "",
    ]
    c = result["chosen_value"]
    lines.append(
        f"**θ\\* = loud {c['loud']} / quiet {c['quiet']} / masked {c['masked']}**："
        f"对每档设 FPR 预算 B∈{{loud 1%, quiet 5%, masked 12%}}，取 "
        f"**FPR 刚压到 B 的最低 θ**；弱档必须更敏感故 B 更大、θ* 更低——"
        f"曲线证明档阈应由数据+预算折中，而非拍脑袋；旧默认 1.3/1.15/1.05 落在邻近。"
    )
    lines += [
        "",
        "## 控制变量",
        "",
        "| 固定 | 值 |",
        "|---|---|",
        f"| 度量 | rank{GATE_RANK} `step_ms` 窗中位比 C1/C0 |",
        f"| 窗 | `[{WINDOW_LO},{WINDOW_HI}]`（长 {WINDOW_LEN}） |",
        f"| victim（旁证） | rank{VICTIM} |",
        "| 自变量 | θ ∈ [1.02, 1.50] step 0.02 |",
        "| FPR 算法 | C0 稳态(step≥50) 滑动窗中位 / 本 run 稳态中位 |",
        "| FPR 预算 | loud 1% / quiet 5% / masked 12% |",
        "",
        "## 推荐 θ\\*",
        "",
        "| 档 | θ\\* | 召回 | FPR | 预算 B | 旧默认 | θ_catch(R=1最严) | Youden |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for dose in ("loud", "quiet", "masked"):
        ch = result["by_dose"][dose]["chosen"]
        catch = ch.get("theta_catch_full_recall")
        catch_s = f"{catch}" if catch is not None else "—"
        lines.append(
            f"| {dose} | **{ch['theta_star']}** | {ch['recall']:.3f} | {ch['fpr']:.4f} | "
            f"{ch['fpr_budget']:.0%} | {LEGACY[dose]} | {catch_s} | {ch.get('theta_youden')} |"
        )
    lines += [
        "",
        "### 选点准则",
        "",
        "1. **主**：θ\\* = min{θ : FPR(θ) ≤ B_dose}（方案「FPR 刚压到可接受的最低阈」）。",
        "2. **B_dose**：loud=1% / quiet=5% / masked=12%——弱档为接住更小 C1/C0 必须更敏感。",
        "3. **辅**：θ_catch = 黄金集全召回的最高 θ；θ_youden = argmax(召回−FPR)。",
        "",
        "## 黄金 case 比值（召回分母）",
        "",
        "| case | dose | run_id | C0 med | C1 med | C1/C0 | C1/C0@r7 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for dose in ("loud", "quiet", "masked"):
        for c in result["by_dose"][dose]["cases_golden"]:
            r7 = f"{c['ratio_v7']:.3f}" if c.get("ratio_v7") is not None else "—"
            lines.append(
                f"| {c['case']} | {dose} | `{c['run_id']}` | {c['c0_med']:.2f} | "
                f"{c['c1_med']:.2f} | **{c['ratio']:.3f}** | {r7} |"
            )
    lines += ["", "## FPR–召回 vs θ（黄金集）", ""]
    for dose in ("loud", "quiet", "masked"):
        lines += [
            f"### {dose}",
            "",
            "| θ | FPR | 召回 | 触发 case |",
            "|---:|---:|---:|---|",
        ]
        star = result["by_dose"][dose]["chosen"]["theta_star"]
        for row in result["by_dose"][dose]["sweep"]:
            hit = ",".join(row["hit_cases"]) if row["hit_cases"] else "—"
            mark = " ←θ\\*" if abs(row["theta"] - star) < 1e-9 else ""
            lines.append(
                f"| {row['theta']:.2f} | {row['fpr']:.4f} | {row['recall']:.3f} | {hit}{mark} |"
            )
        lines.append("")

    if result.get("loud_expanded"):
        le = result["loud_expanded"]
        lines += [
            "## loud 扩充分母（辅表）",
            "",
            f"黄金 2 case + {le['n_cases'] - 2} 个正式 loud；"
            f"θ\\*_expand={le['chosen']['theta_star']} "
            f"(recall={le['chosen']['recall']:.3f}, FPR={le['chosen']['fpr']:.4f})。",
            "",
            "| case | C1/C0 | ≥θ\\*_loud? | ≥旧1.3? |",
            "|---|---:|---|---|",
        ]
        ts = result["by_dose"]["loud"]["chosen"]["theta_star"]
        for c in le["cases"]:
            lines.append(
                f"| {c['case']} | {c['ratio']:.3f} | "
                f"{'Y' if c['ratio'] >= ts else 'N'} | "
                f"{'Y' if c['ratio'] >= 1.3 else 'N'} |"
            )
        lines.append("")

    lines += ["## 图", ""]
    for p in fig_paths:
        lines.append(f"- `{Path(p).name}`")
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "python3 project/probing-huawei/scripts/fail-slow/param_calib/1a_dose_threshold.py \\",
        "  --results-root project/probing-huawei/results/ascend-ais \\",
        "  --out project/probing-huawei/results/ascend-ais/param_calib/1A_dose_threshold",
        "```",
        "",
    ]
    (out_dir / "PARAM.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results-root",
        default="project/probing-huawei/results/ascend-ais",
        type=Path,
    )
    ap.add_argument(
        "--out",
        default="project/probing-huawei/results/ascend-ais/param_calib/1A_dose_threshold",
        type=Path,
    )
    ap.add_argument("--theta-step", type=float, default=THETA_STEP)
    args = ap.parse_args()
    root: Path = args.results_root
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    th_list = thetas(step=args.theta_step)
    by_dose: dict = {}

    # 共享 C0 FPR 池：黄金三档 + loud 扩充
    fpr_run_ids: list[str] = []
    for dose in ("loud", "quiet", "masked"):
        fpr_run_ids += [rid for _, rid in GOLDEN[dose]]
    fpr_run_ids += [rid for _, rid in LOUD_EXPAND]
    self_ratios = collect_c0_fpr_ratios(root, fpr_run_ids)

    for dose in ("loud", "quiet", "masked"):
        golden_cases: list[CaseRatio] = []
        for case, rid in GOLDEN[dose]:
            cr = load_case_ratio(root, case, dose, rid, golden=True)
            if cr is None:
                raise SystemExit(f"missing golden data: {dose} {case} {rid}")
            golden_cases.append(cr)

        sweep = []
        for th in th_list:
            hits = [c.case for c in golden_cases if c.ratio >= th]
            sweep.append(
                {
                    "theta": th,
                    "fpr": fpr_at(self_ratios, th),
                    "recall": recall_at(golden_cases, th),
                    "hit_cases": hits,
                    "n_fpr_trials": len(self_ratios),
                    "n_recall_cases": len(golden_cases),
                }
            )
        chosen = choose_theta(sweep, DOSE_FPR_BUDGET[dose], dose)
        by_dose[dose] = {
            "cases_golden": [asdict(c) for c in golden_cases],
            "sweep": sweep,
            "chosen": chosen,
            "n_fpr_trials": len(self_ratios),
            "fpr_self_ratio_summary": {
                "n": len(self_ratios),
                "p50": statistics.median(self_ratios) if self_ratios else None,
                "p95": (
                    sorted(self_ratios)[int(0.95 * (len(self_ratios) - 1))]
                    if self_ratios
                    else None
                ),
                "max": max(self_ratios) if self_ratios else None,
            },
        }

    loud_all = [CaseRatio(**c) for c in by_dose["loud"]["cases_golden"]]
    for case, rid in LOUD_EXPAND:
        cr = load_case_ratio(root, case, "loud", rid, golden=False)
        if cr:
            loud_all.append(cr)
    sweep_exp = [
        {
            "theta": th,
            "fpr": fpr_at(self_ratios, th),
            "recall": recall_at(loud_all, th),
            "hit_cases": [c.case for c in loud_all if c.ratio >= th],
        }
        for th in th_list
    ]
    loud_expanded = {
        "n_cases": len(loud_all),
        "cases": [asdict(c) for c in loud_all],
        "sweep": sweep_exp,
        "chosen": choose_theta(sweep_exp, DOSE_FPR_BUDGET["loud"], "loud"),
    }

    chosen_value = {
        dose: by_dose[dose]["chosen"]["theta_star"] for dose in ("loud", "quiet", "masked")
    }
    result = {
        "param": "d1_min_ratio",
        "exp_id": "1A_dose_threshold",
        "swept_range": {
            "lo": THETA_LO,
            "hi": THETA_HI,
            "step": args.theta_step,
            "values": th_list,
        },
        "chosen_value": chosen_value,
        "legacy_defaults": LEGACY,
        "fpr_budgets": DOSE_FPR_BUDGET,
        "ground_truth_source": {
            "fpr": (
                "C0_baseline steady sliding-window median / run steady median "
                f"(rank{GATE_RANK} step_ms, step≥{STEADY_LO})"
            ),
            "recall": (
                f"C1/C0 window median ≥ θ (rank{GATE_RANK}; window [{WINDOW_LO},{WINDOW_HI}])"
            ),
            "victim_side": f"rank{VICTIM} C1/C0 as side evidence",
        },
        "selection_rule": (
            "per dose: θ*=min{θ: FPR(θ)≤B_dose}; "
            "B={loud:1%, quiet:5%, masked:12%}"
        ),
        "controls": {
            "window": [WINDOW_LO, WINDOW_HI],
            "gate_rank": GATE_RANK,
            "victim_rank": VICTIM,
            "metric": "step_ms_median_ratio",
            "single_iv": "theta",
            "steady_lo": STEADY_LO,
        },
        "supports_design": (
            "Dose-tier θ* from FPR-budget floors on C0 self-ratio curve; "
            "weaker doses get higher B → lower θ*; legacy 1.3/1.15/1.05 sit nearby."
        ),
        "by_dose": by_dose,
        "loud_expanded": loud_expanded,
    }

    (out / "PARAM.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (out / "sweep.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dose", "theta", "fpr", "recall", "hit_cases"])
        for dose in ("loud", "quiet", "masked"):
            for row in by_dose[dose]["sweep"]:
                w.writerow(
                    [
                        dose,
                        row["theta"],
                        f"{row['fpr']:.6f}",
                        f"{row['recall']:.6f}",
                        "|".join(row["hit_cases"]),
                    ]
                )

    fig_paths = plot_curves(out, by_dose)
    write_param_md(out, result, fig_paths)

    print("=== ①-A θ* ===")
    for dose in ("loud", "quiet", "masked"):
        ch = by_dose[dose]["chosen"]
        print(
            f"  {dose}: θ*={ch['theta_star']}  recall={ch['recall']:.3f}  "
            f"FPR={ch['fpr']:.4f}  B={ch['fpr_budget']:.0%}  "
            f"(legacy {LEGACY[dose]}, catch={ch.get('theta_catch_full_recall')})"
        )
        for c in by_dose[dose]["cases_golden"]:
            print(f"    {c['case']}: C1/C0={c['ratio']:.3f}")
    print(f"out: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
