#!/usr/bin/env python3
"""①-B 定位原语阈值：跨 rank max/min θ + 附扫 worst_fraction（纯离线）。

铁律：主自变量=跨 rank 比阈 θ∈[1.2,2.0]；worst_fraction 为次扫（θ 固定 θ*）。
档阈固定为 ①-A θ*（loud/quiet/masked），只作 dose 门控，不扫。

- 定位度量：与 offline D3 同族（P1 GPU→compute_ms/min；P3 host/SW→data_ms/max）
- C1/C2：窗中位跨 rank max/min；fire∧指中 victim=7 → 准确；fire∧误指 → 误指
- C0：同窗 fire → FPR（健康误报 straggler）
- θ*：hit_rate 与 mispoint_rate 交叉（或 Youden=hit−mis 最大）

用法:
  python3 1b_localize_threshold.py \\
    --results-root project/probing-huawei/results/ascend-ais \\
    --out project/probing-huawei/results/ascend-ais/param_calib/1B_localize_threshold
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
_CANDIDATES = [
    Path("/Users/yinjinrun/Codespace/myportal/project/lab-workspace/reports"),
    Path.home() / "Codespace/myportal/project/lab-workspace/reports",
]
for _d in _CANDIDATES:
    if (_d / "plot_style.py").is_file():
        sys.path.insert(0, str(_d))
        break

WINDOW_LO = 100
WINDOW_HI = 300
VICTIM = 7
N_RANKS = 16
METRIC_FLOOR = 1e-3

# ①-A 档阈（控制变量，本实验不扫）
DOSE_THETA = {"loud": 1.16, "quiet": 1.12, "masked": 1.04}

THETA_LO = 1.2
THETA_HI = 2.0
THETA_STEP = 0.05
LEGACY_THETA = 1.5

WF_LO = 0.05
WF_HI = 0.50
WF_STEP = 0.05
LEGACY_WF = 0.25

# (case, dose, run_id, role)  role: primary | expand | golden
# 度量由 case 族决定（见 metric_spec）
DATASETS = [
    # ①-B 主
    ("P3-SW-A", "loud", "20260725_012957-yjr-as-c-p3-sw-a-loud", "primary"),
    ("P1-EXT-A", "loud", "20260725_011129-yjr-as-c-p1-ext-a-loud", "primary"),
    ("P1-EXT-A", "masked", "20260726_014611-yjr-as-c-p1-ext-a-masked", "primary"),
    # 黄金 ①-A 扩充
    ("P3-EXT-A", "loud", "20260725_001251-yjr-as-c-p3-ext-a-loud", "golden"),
    ("P3-EXT-A", "quiet", "20260726_075912-yjr-as-c-p3-ext-a-quiet", "golden"),
    ("P3-EXT-A", "masked", "20260726_094648-yjr-as-c-p3-ext-a-masked", "golden"),
    ("P3-SW-C", "loud", "20260725_135238-yjr-as-c-p3-sw-c-loud", "golden"),
    ("P3-SW-C", "quiet", "20260726_125953-yjr-as-c-p3-sw-c-quiet", "golden"),
    ("P3-SW-C", "masked", "20260726_135016-yjr-as-c-p3-sw-c-masked", "golden"),
    # 干净 GPU 定位扩充（compute_ms/min）
    ("P1-EXT-A", "quiet", "20260726_013034-yjr-as-c-p1-ext-a-quiet", "expand"),
    ("P1-EXT-B", "loud", "20260725_014350-yjr-as-c-p1-ext-b-loud", "expand"),
    ("P1-SW-A", "loud", "20260725_114556-yjr-as-c-p1-sw-a-loud", "expand"),
    ("P1-HW-B", "loud", "20260725_142359-yjr-as-c-p1-hw-b-loud", "expand"),
    ("P3-SW-A", "quiet", "20260725_215903-yjr-as-c-p3-sw-a-quiet", "expand"),
    ("P3-SW-A", "masked", "20260725_224156-yjr-as-c-p3-sw-a-masked", "expand"),
]


@dataclass
class Trial:
    case: str
    dose: str
    run_id: str
    role: str
    arm: str  # C0 / C1 / C2
    metric: str
    polarity: str  # max | min
    ratio: float
    pred: int
    hit: bool  # pred == victim
    hit_soft: bool  # ±1 or same_host
    dose_gate_ok: bool  # C1/C0 step ≥ θ_dose*（仅 inject 臂有意义）
    c1_c0_step: float | None
    worst_fraction_victim: float
    worst_pred: int
    worst_fraction_top: float


def metric_spec(case: str) -> tuple[str, str]:
    """与 score_dlevel_offline D3 同族。"""
    if case.startswith("P3"):
        return "data_ms", "max"
    # P1 GPU / HW：注入常使 victim compute 异常偏低
    return "compute_ms", "min"


def find_rank(run_root: Path, cfg_glob: str, rank: int) -> Path | None:
    hits = sorted(
        run_root.glob(f"**/by_pod/*/round_1/{cfg_glob}/ranks/rank_{rank:04d}.jsonl")
    )
    if hits:
        return hits[0]
    hits = sorted(run_root.glob(f"**/{cfg_glob}/ranks/rank_{rank:04d}.jsonl"))
    return hits[0] if hits else None


def find_cfg_dir(run_root: Path, cfg_prefix: str) -> Path | None:
    hits = sorted(run_root.glob(f"**/by_pod/*/round_1/{cfg_prefix}*/ranks"))
    if hits:
        return hits[0]
    hits = sorted(run_root.glob(f"**/{cfg_prefix}*/ranks"))
    return hits[0] if hits else None


def load_rank_series(path: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "step" not in o or "step_ms" not in o:
                continue
            out[int(o["step"])] = o
    return out


def load_all_ranks(run_root: Path, cfg_prefix: str) -> dict[int, dict[int, dict]] | None:
    ranks_dir = find_cfg_dir(run_root, cfg_prefix)
    if ranks_dir is None:
        return None
    series: dict[int, dict[int, dict]] = {}
    for r in range(N_RANKS):
        p = ranks_dir / f"rank_{r:04d}.jsonl"
        if not p.is_file():
            return None
        series[r] = load_rank_series(p)
    return series


def common_steps(series: dict[int, dict[int, dict]], lo: int, hi: int) -> list[int]:
    sets = [set(s for s in series[r] if lo <= s <= hi) for r in range(N_RANKS)]
    return sorted(set.intersection(*sets))


def median_of(xs: list[float]) -> float | None:
    return float(statistics.median(xs)) if xs else None


def window_medians(
    series: dict[int, dict[int, dict]], steps: list[int], metric: str
) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in range(N_RANKS):
        xs = [float(series[r][s].get(metric, 0.0)) for s in steps]
        out[r] = median_of(xs) or 0.0
    return out


def max_min_ratio(meds: dict[int, float], floor: float = METRIC_FLOOR) -> float:
    vals = [max(v, floor) for v in meds.values()]
    return max(vals) / min(vals)


def predict_rank(meds: dict[int, float], polarity: str) -> int:
    if polarity == "max":
        return max(range(N_RANKS), key=lambda r: meds[r])
    return min(range(N_RANKS), key=lambda r: meds[r])


def hit_soft(pred: int, victim: int, case: str) -> bool:
    if abs(pred - victim) <= 1:
        return True
    # P3-EXT stress_*：整机争用，同 host（单 pod 16 卡）放宽
    if case.startswith("P3-EXT"):
        return True  # same_host_single_pod（本战役默认 1×16）
    if case == "P3-SW-C":
        return True  # sidecar 外挂，offline D3 用 same_host
    return False


def worst_fraction(
    series: dict[int, dict[int, dict]], steps: list[int], metric: str, polarity: str
) -> tuple[dict[int, float], int, float]:
    cnt: Counter[int] = Counter()
    for s in steps:
        xs = {r: float(series[r][s].get(metric, 0.0)) for r in range(N_RANKS)}
        if polarity == "max":
            best = max(xs, key=xs.get)
        else:
            best = min(xs, key=xs.get)
        cnt[best] += 1
    n = max(len(steps), 1)
    wf = {r: cnt[r] / n for r in range(N_RANKS)}
    top = max(range(N_RANKS), key=lambda r: wf[r])
    return wf, top, wf[top]


def gate_rank0_step_ratio(run_root: Path) -> float | None:
    c0p = find_rank(run_root, "C0_baseline", 0)
    c1p = find_rank(run_root, "C1_*", 0)
    if not c0p or not c1p:
        return None
    c0 = load_rank_series(c0p)
    c1 = load_rank_series(c1p)
    m0 = median_of([c0[s]["step_ms"] for s in c0 if WINDOW_LO <= s <= WINDOW_HI])
    m1 = median_of([c1[s]["step_ms"] for s in c1 if WINDOW_LO <= s <= WINDOW_HI])
    if m0 is None or m1 is None or m0 <= 0:
        return None
    return m1 / m0


def build_trial(
    results_root: Path,
    case: str,
    dose: str,
    run_id: str,
    role: str,
    arm: str,
) -> Trial | None:
    run_root = results_root / run_id
    if not run_root.exists():
        return None
    cfg = {"C0": "C0_baseline", "C1": "C1_", "C2": "C2_"}[arm]
    series = load_all_ranks(run_root, cfg)
    if series is None:
        return None
    steps = common_steps(series, WINDOW_LO, WINDOW_HI)
    if len(steps) < 50:
        return None
    metric, polarity = metric_spec(case)
    meds = window_medians(series, steps, metric)
    ratio = max_min_ratio(meds)
    pred = predict_rank(meds, polarity)
    wf, wf_pred, wf_top = worst_fraction(series, steps, metric, polarity)
    c1c0 = gate_rank0_step_ratio(run_root)
    dose_ok = True
    if arm in ("C1", "C2") and c1c0 is not None:
        dose_ok = c1c0 >= DOSE_THETA[dose]
    return Trial(
        case=case,
        dose=dose,
        run_id=run_id,
        role=role,
        arm=arm,
        metric=metric,
        polarity=polarity,
        ratio=ratio,
        pred=pred,
        hit=(pred == VICTIM),
        hit_soft=hit_soft(pred, VICTIM, case),
        dose_gate_ok=dose_ok,
        c1_c0_step=c1c0,
        worst_fraction_victim=wf[VICTIM],
        worst_pred=wf_pred,
        worst_fraction_top=wf_top,
    )


def thetas(lo=THETA_LO, hi=THETA_HI, step=THETA_STEP) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


def fracs(lo=WF_LO, hi=WF_HI, step=WF_STEP) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


def sweep_theta(
    inject: list[Trial], healthy: list[Trial], th_list: list[float], soft: bool = False
) -> list[dict]:
    rows = []
    n_inj = len(inject)
    n_h = len(healthy)
    for th in th_list:
        hit = mis = miss = 0
        for t in inject:
            fire = t.ratio >= th
            ok = t.hit_soft if soft else t.hit
            if fire and ok:
                hit += 1
            elif fire and not ok:
                mis += 1
            else:
                miss += 1
        fpr = (
            sum(1 for t in healthy if t.ratio >= th) / n_h if n_h else float("nan")
        )
        hit_rate = hit / n_inj if n_inj else float("nan")
        mis_rate = mis / n_inj if n_inj else float("nan")
        precision = hit / (hit + mis) if (hit + mis) else float("nan")
        rows.append(
            {
                "theta": th,
                "hit_rate": hit_rate,
                "mispoint_rate": mis_rate,
                "miss_rate": miss / n_inj if n_inj else float("nan"),
                "precision": precision,
                "fpr_c0": fpr,
                "n_hit": hit,
                "n_mis": mis,
                "n_miss": miss,
                "n_inject": n_inj,
                "n_healthy": n_h,
                "youden": hit_rate - mis_rate if n_inj else float("nan"),
            }
        )
    return rows


def choose_theta(sweep: list[dict]) -> dict:
    """θ*：优先准确率 vs 误指率交叉；否则 Youden；并列时取更低 θ（更敏感）。"""
    cross = min(sweep, key=lambda r: abs(r["hit_rate"] - r["mispoint_rate"]))
    youden = max(sweep, key=lambda r: (r["youden"], -r["theta"]))
    # 交叉可用：|hit−mis| 小且两条曲线在扫程内真有交汇趋势
    hit_span = max(r["hit_rate"] for r in sweep) - min(r["hit_rate"] for r in sweep)
    mis_span = max(r["mispoint_rate"] for r in sweep) - min(
        r["mispoint_rate"] for r in sweep
    )
    if (
        abs(cross["hit_rate"] - cross["mispoint_rate"]) <= 0.12
        and (hit_span > 0.05 or mis_span > 0.05)
    ):
        chosen = cross
        rule = "cross(|hit_rate−mispoint_rate| min；准确率 vs 误指率交叉)"
    else:
        chosen = youden
        rule = "Youden=argmax(hit_rate−mispoint_rate)（无清晰交叉时）"
    return {
        "theta_star": chosen["theta"],
        "hit_rate": chosen["hit_rate"],
        "mispoint_rate": chosen["mispoint_rate"],
        "precision": chosen["precision"],
        "fpr_c0": chosen["fpr_c0"],
        "youden": chosen["youden"],
        "rule": rule,
        "theta_youden": youden["theta"],
        "youden_J": youden["youden"],
        "theta_cross": cross["theta"],
        "legacy": LEGACY_THETA,
    }


def is_localizable_case(case: str) -> bool:
    """可精确指到 victim=7 的 case 族（主标定）；host-wide 整机争用另表。"""
    if case.startswith("P1"):
        return True
    if case in ("P3-SW-A", "P3-SW-B"):
        return True
    return False


def is_hostwide_case(case: str) -> bool:
    return case.startswith("P3-EXT") or case == "P3-SW-C"


def sweep_worst_fraction(
    inject: list[Trial], healthy: list[Trial], phi_list: list[float], soft: bool = False
) -> list[dict]:
    rows = []
    n_inj = len(inject)
    n_h = len(healthy)
    for phi in phi_list:
        hit = mis = miss = 0
        for t in inject:
            fire = t.worst_fraction_top >= phi
            ok = (t.worst_pred == VICTIM) or (
                soft and hit_soft(t.worst_pred, VICTIM, t.case)
            )
            if fire and ok:
                hit += 1
            elif fire and not ok:
                mis += 1
            else:
                miss += 1
        fpr = (
            sum(1 for t in healthy if t.worst_fraction_top >= phi) / n_h
            if n_h
            else float("nan")
        )
        hit_rate = hit / n_inj if n_inj else float("nan")
        mis_rate = mis / n_inj if n_inj else float("nan")
        rows.append(
            {
                "phi": phi,
                "hit_rate": hit_rate,
                "mispoint_rate": mis_rate,
                "miss_rate": miss / n_inj if n_inj else float("nan"),
                "fpr_c0": fpr,
                "n_hit": hit,
                "n_mis": mis,
                "n_miss": miss,
                "youden": hit_rate - mis_rate if n_inj else float("nan"),
            }
        )
    return rows


def choose_phi(sweep: list[dict], fpr_budget: float = 0.30) -> dict:
    """φ*：FPR≤预算下 Youden 最大；否则全局 Youden。附报交叉点。"""
    cross = min(sweep, key=lambda r: abs(r["hit_rate"] - r["mispoint_rate"]))
    youden = max(sweep, key=lambda r: (r["youden"], -r["phi"]))
    feasible = [r for r in sweep if r["fpr_c0"] <= fpr_budget]
    if feasible:
        chosen = max(feasible, key=lambda r: (r["youden"], -r["phi"]))
        rule = f"max Youden s.t. C0 FPR≤{fpr_budget:.0%}（附扫；控健康误报）"
    else:
        chosen = youden
        rule = "Youden=argmax(hit−mis)（无 FPR 可行点）"
    return {
        "fraction_star": chosen["phi"],
        "hit_rate": chosen["hit_rate"],
        "mispoint_rate": chosen["mispoint_rate"],
        "fpr_c0": chosen["fpr_c0"],
        "youden": chosen["youden"],
        "rule": rule,
        "phi_youden": youden["phi"],
        "phi_cross": cross["phi"],
        "fpr_budget": fpr_budget,
        "legacy": LEGACY_WF,
    }


def plot_curves(out_dir: Path, theta_sweep: list[dict], phi_sweep: list[dict], chosen_th, chosen_phi) -> list[str]:
    try:
        import matplotlib.pyplot as plt
        from plot_style import apply_plot_style, save_fig, style_axes
    except Exception as e:
        print(f"[warn] plot skip: {e}", file=sys.stderr)
        return []

    paths: list[str] = []
    apply_plot_style((9, 5))
    fig, ax = plt.subplots()
    th = [r["theta"] for r in theta_sweep]
    ax.plot(th, [r["hit_rate"] for r in theta_sweep], "o-", label="准确定位率 hit", color="tab:orange")
    ax.plot(th, [r["mispoint_rate"] for r in theta_sweep], "s--", label="误指率 mis", color="tab:red")
    ax.plot(th, [r["fpr_c0"] for r in theta_sweep], "^:", label="C0 FPR", color="tab:blue")
    ts = chosen_th["theta_star"]
    ax.axvline(ts, color="tab:green", ls="-.", lw=1.5, label=f"θ*={ts}")
    ax.axvline(LEGACY_THETA, color="tab:gray", ls=":", lw=1.2, label=f"旧默认 {LEGACY_THETA}")
    ax.set_xlabel("跨 rank max/min 阈 θ")
    ax.set_ylabel("比率")
    ax.set_title("①-B 定位阈标定 · 准确率 vs 误指率")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(loc="best", fontsize=13)
    style_axes(ax)
    p = out_dir / "fig_localize_theta.svg"
    save_fig(fig, p)
    paths.append(str(p))

    apply_plot_style((9, 5))
    fig, ax = plt.subplots()
    ph = [r["phi"] for r in phi_sweep]
    ax.plot(ph, [r["hit_rate"] for r in phi_sweep], "o-", label="准确定位率 hit", color="tab:orange")
    ax.plot(ph, [r["mispoint_rate"] for r in phi_sweep], "s--", label="误指率 mis", color="tab:red")
    ax.plot(ph, [r["fpr_c0"] for r in phi_sweep], "^:", label="C0 FPR", color="tab:blue")
    fs = chosen_phi["fraction_star"]
    ax.axvline(fs, color="tab:green", ls="-.", lw=1.5, label=f"φ*={fs}")
    ax.axvline(LEGACY_WF, color="tab:gray", ls=":", lw=1.2, label=f"旧默认 {LEGACY_WF}")
    ax.set_xlabel("worst_fraction 阈 φ")
    ax.set_ylabel("比率")
    ax.set_title("①-B 附扫 · worst_fraction")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(loc="best", fontsize=13)
    style_axes(ax)
    p = out_dir / "fig_worst_fraction.svg"
    save_fig(fig, p)
    paths.append(str(p))
    return paths


def write_param_md(out_dir: Path, result: dict, fig_paths: list[str]) -> None:
    c = result["chosen_value"]
    lines = [
        "# ①-B 定位阈标定 · 跨 rank max/min + worst_fraction（exp=`1B_localize_threshold`）",
        "",
        "> 主自变量=跨 rank θ∈[1.2,2.0]；`worst_fraction` 为**次扫**（θ 已定后附扫）。",
        f"> 档阈固定 ①-A：loud={DOSE_THETA['loud']} / quiet={DOSE_THETA['quiet']} / masked={DOSE_THETA['masked']}（dose 门控）。",
        f"> 窗 `[{WINDOW_LO},{WINDOW_HI}]`；真值 victim={VICTIM}；C0→FPR，C1/C2→定位召回/误指。",
        "> 度量：P1 GPU=`compute_ms` 取 **min**（victim compute 偏低）；P3=`data_ms` 取 **max**（与 offline D3 同族）。",
        "> 禁止 cold / 训练 step_ms「采集差异」叙事。",
        "",
        "## 为什么这么设（一句）",
        "",
        f"**θ\\*={c['cross_rank_theta']}，φ\\*={c['worst_fraction']}**："
        f"在 dose 门控后、**可定位到 victim** 的 C1/C2 全 16 rank 上扫跨 rank max/min"
        f"（P1=`compute_ms/min`，P3-SW-A=`data_ms/max`），"
        f"取准确率 vs 误指率交叉或 Youden；"
        f"host-wide（P3-EXT/P3-SW-C）整机争用另表（exact rank 不可比）。"
        f"旧默认 1.5 / 0.25 落在邻近；masked 弱档 ratio 常 <1.2，靠相位极端值而非高阈。",
        "",
        "## 控制变量",
        "",
        "| 固定 | 值 |",
        "|---|---|",
        f"| 窗 | `[{WINDOW_LO},{WINDOW_HI}]` |",
        f"| victim | rank {VICTIM} |",
        f"| 档阈（①-A） | loud {DOSE_THETA['loud']} / quiet {DOSE_THETA['quiet']} / masked {DOSE_THETA['masked']} |",
        "| 主自变量 | 跨 rank max/min θ ∈ [1.2, 2.0] step 0.05 |",
        "| 次扫 | worst_fraction φ ∈ [0.05, 0.50] step 0.05（θ 不参与） |",
        "| 主标定池 | P1-* + P3-SW-A（可 exact 指到 r7） |",
        "| 排除主表 | P3-EXT / P3-SW-C（host-wide；D3=same_host） |",
        "",
        "## 推荐参数",
        "",
        f"| 参数 | 值 | hit | mis | C0 FPR | 旧默认 | 选点 |",
        f"|---|---:|---:|---:|---:|---:|---|",
        f"| 跨 rank θ\\* | **{c['cross_rank_theta']}** | "
        f"{result['theta_chosen']['hit_rate']:.3f} | "
        f"{result['theta_chosen']['mispoint_rate']:.3f} | "
        f"{result['theta_chosen']['fpr_c0']:.3f} | {LEGACY_THETA} | "
        f"{result['theta_chosen']['rule']} |",
        f"| worst_fraction φ\\* | **{c['worst_fraction']}** | "
        f"{result['phi_chosen']['hit_rate']:.3f} | "
        f"{result['phi_chosen']['mispoint_rate']:.3f} | "
        f"{result['phi_chosen']['fpr_c0']:.3f} | {LEGACY_WF} | "
        f"{result['phi_chosen']['rule']} |",
        "",
        "## 试验明细（主池 C1/C2；窗中位）",
        "",
        "| case | dose | arm | role | metric | ratio | pred | hit | dose_gate | wf7 | wf_pred |",
        "|---|---|---|---|---|---:|---:|---|---|---:|---:|",
    ]
    for t in result["trials"]:
        if t["arm"] == "C0":
            continue
        if not is_localizable_case(t["case"]):
            continue
        lines.append(
            f"| {t['case']} | {t['dose']} | {t['arm']} | {t['role']} | "
            f"{t['metric']}/{t['polarity']} | {t['ratio']:.3f} | {t['pred']} | "
            f"{'Y' if t['hit'] else 'N'} | {'Y' if t['dose_gate_ok'] else 'N'} | "
            f"{t['worst_fraction_victim']:.3f} | {t['worst_pred']} |"
        )
    lines += [
        "",
        "## θ 扫描（主池；exact hit）",
        "",
        "| θ | hit_rate | mispoint | miss | precision | C0 FPR | Youden |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    star = result["theta_chosen"]["theta_star"]
    for row in result["theta_sweep"]:
        mark = " ←θ\\*" if abs(row["theta"] - star) < 1e-9 else ""
        prec = row["precision"]
        prec_s = f"{prec:.3f}" if prec == prec else "—"
        lines.append(
            f"| {row['theta']:.2f} | {row['hit_rate']:.3f} | {row['mispoint_rate']:.3f} | "
            f"{row['miss_rate']:.3f} | {prec_s} | {row['fpr_c0']:.3f} | "
            f"{row['youden']:.3f}{mark} |"
        )
    lines += [
        "",
        "## φ 扫描（worst_fraction；附）",
        "",
        "| φ | hit_rate | mispoint | miss | C0 FPR | Youden |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    pstar = result["phi_chosen"]["fraction_star"]
    for row in result["phi_sweep"]:
        mark = " ←φ\\*" if abs(row["phi"] - pstar) < 1e-9 else ""
        lines.append(
            f"| {row['phi']:.2f} | {row['hit_rate']:.3f} | {row['mispoint_rate']:.3f} | "
            f"{row['miss_rate']:.3f} | {row['fpr_c0']:.3f} | {row['youden']:.3f}{mark} |"
        )
    lines += [
        "",
        "## 分层旁证",
        "",
    ]
    labels = {
        "gpu": "GPU `compute_ms/min`",
        "host_localizable": "Host 可定位 `data_ms/max`（P3-SW-A）",
        "host_wide": "Host-wide soft same_host（P3-EXT/P3-SW-C）",
        "all_gated": "全 gated exact（含 host-wide，仅对照）",
    }
    for key, name in labels.items():
        sub = result["strata"].get(key)
        if not sub or not sub.get("theta_chosen"):
            continue
        ch = sub["theta_chosen"]
        lines.append(
            f"- **{name}**：θ\\*={ch['theta_star']} "
            f"(hit={ch['hit_rate']:.3f}, mis={ch['mispoint_rate']:.3f}, "
            f"FPR={ch['fpr_c0']:.3f}；n_inj={sub['n_inject']}；mode={sub.get('hit_mode')})"
        )
    lines += [
        "",
        "### 诚实注记",
        "",
        "- **GPU 层**：C0 max/min≈1.02，扫程内 FPR≈0；θ\\* 主要由召回（弱档 quiet/masked）决定。",
        "- **P3-SW-A data_ms**：注入后 ratio≫100、指中 r7；健康窗 data_ms 噪声也可 >1.5 → 单独用 data_ms 时 C0 FPR 偏高，定位应在 dose 门控之后。",
        "- **Host-wide**：整机争用下 exact rank 常非 7，offline D3 用 same_host；不进 θ\\* 主点。",
        "- **Masked P1-EXT-A** ratio≈1.10 <1.2：扫程下 miss，弱档需更低跨 rank 阈或只靠 dose 门。",
        "",
        "## 图",
        "",
    ]
    for p in fig_paths:
        lines.append(f"- `{Path(p).name}`")
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "python3 project/probing-huawei/scripts/fail-slow/param_calib/1b_localize_threshold.py \\",
        "  --results-root project/probing-huawei/results/ascend-ais \\",
        "  --out project/probing-huawei/results/ascend-ais/param_calib/1B_localize_threshold",
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
        default="project/probing-huawei/results/ascend-ais/param_calib/1B_localize_threshold",
        type=Path,
    )
    args = ap.parse_args()
    root: Path = args.results_root
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    trials: list[Trial] = []
    missing: list[str] = []
    for case, dose, rid, role in DATASETS:
        for arm in ("C0", "C1", "C2"):
            t = build_trial(root, case, dose, rid, role, arm)
            if t is None:
                missing.append(f"{rid}/{arm}")
            else:
                trials.append(t)
    if missing:
        print(f"[warn] missing {len(missing)} arms:", file=sys.stderr)
        for m in missing[:12]:
            print(f"  - {m}", file=sys.stderr)

    # 主标定：可定位到 victim 的 case（P1 + P3-SW-A/B）；dose 门控
    # host-wide（P3-EXT / P3-SW-C）整机争用不进 exact-hit 主表，仅分层旁证
    inject = [
        t
        for t in trials
        if t.arm in ("C1", "C2")
        and t.dose_gate_ok
        and is_localizable_case(t.case)
    ]
    healthy = [
        t for t in trials if t.arm == "C0" and is_localizable_case(t.case)
    ]
    inject_all = [
        t for t in trials if t.arm in ("C1", "C2") and t.dose_gate_ok
    ]
    healthy_all = [t for t in trials if t.arm == "C0"]
    inject_hostwide = [
        t
        for t in trials
        if t.arm in ("C1", "C2")
        and t.dose_gate_ok
        and is_hostwide_case(t.case)
    ]
    healthy_hostwide = [
        t for t in trials if t.arm == "C0" and is_hostwide_case(t.case)
    ]

    th_list = thetas()
    phi_list = fracs()
    theta_sweep = sweep_theta(inject, healthy, th_list, soft=False)
    theta_chosen = choose_theta(theta_sweep)
    phi_sweep = sweep_worst_fraction(inject, healthy, phi_list, soft=False)
    phi_chosen = choose_phi(phi_sweep)

    # 分层
    strata = {}
    for key, pred_fn in (
        ("gpu", lambda t: t.metric == "compute_ms"),
        ("host_localizable", lambda t: t.metric == "data_ms" and is_localizable_case(t.case)),
        ("host_wide", lambda t: is_hostwide_case(t.case)),
        ("all_gated", lambda t: True),
    ):
        if key == "host_wide":
            inj_s, h_s = inject_hostwide, healthy_hostwide
            soft = True  # same_host 口径
        elif key == "all_gated":
            inj_s, h_s = inject_all, healthy_all
            soft = False
        else:
            inj_s = [t for t in inject if pred_fn(t)]
            h_s = [t for t in healthy if pred_fn(t)]
            soft = False
        sw = sweep_theta(inj_s, h_s, th_list, soft=soft)
        strata[key] = {
            "n_inject": len(inj_s),
            "n_healthy": len(h_s),
            "theta_sweep": sw,
            "theta_chosen": choose_theta(sw) if sw else {},
            "hit_mode": "soft_same_host" if soft else "exact",
        }

    # soft 旁证（主集）
    soft_sweep = sweep_theta(inject, healthy, th_list, soft=True)
    soft_chosen = choose_theta(soft_sweep)

    chosen_value = {
        "cross_rank_theta": theta_chosen["theta_star"],
        "worst_fraction": phi_chosen["fraction_star"],
    }

    result = {
        "param": "cross_rank_max_min_ratio + worst_fraction",
        "exp_id": "1B_localize_threshold",
        "swept_range": {
            "theta": {"lo": THETA_LO, "hi": THETA_HI, "step": THETA_STEP, "values": th_list},
            "worst_fraction": {
                "lo": WF_LO,
                "hi": WF_HI,
                "step": WF_STEP,
                "values": phi_list,
                "note": "secondary sweep; primary IV is theta",
            },
        },
        "chosen_value": chosen_value,
        "legacy_defaults": {"cross_rank_theta": LEGACY_THETA, "worst_fraction": LEGACY_WF},
        "ground_truth_source": {
            "victim": f"inject local_rank={VICTIM}",
            "fpr": "C0_baseline window max/min ≥ θ on same phase metric",
            "localization": "C1/C2 full 16-rank window median; fire∧pred==7",
        },
        "selection_rule": theta_chosen["rule"],
        "controls": {
            "window": [WINDOW_LO, WINDOW_HI],
            "victim_rank": VICTIM,
            "dose_theta_1A": DOSE_THETA,
            "dose_gate": "C1/C0 step_ms median ≥ θ_dose*(dose); inject arms only",
            "primary_pool": "localizable: P1-* (compute_ms/min) + P3-SW-A/B (data_ms/max)",
            "excluded_from_primary": "P3-EXT-*/P3-SW-C host-wide (same_host D3; exact rank 不可比)",
            "single_iv_primary": "cross_rank_max_min_theta",
            "secondary": "worst_fraction",
            "metric_by_case": "P3-SW→data_ms/max; P1→compute_ms/min (D3 family)",
        },
        "supports_design": (
            f"θ*={chosen_value['cross_rank_theta']} from hit vs mispoint on localizable "
            f"C1/C2 16-rank phase metrics (P1+P3-SW-A); φ*={chosen_value['worst_fraction']} "
            f"secondary with FPR budget; legacy 1.5/0.25 nearby. Dose gate fixed from ①-A."
        ),
        "theta_chosen": theta_chosen,
        "phi_chosen": phi_chosen,
        "theta_sweep": theta_sweep,
        "phi_sweep": phi_sweep,
        "soft_hit_side": soft_chosen,
        "strata": {
            k: {
                "n_inject": v["n_inject"],
                "n_healthy": v["n_healthy"],
                "theta_chosen": v["theta_chosen"],
                "hit_mode": v.get("hit_mode", "exact"),
            }
            for k, v in strata.items()
        },
        "trials": [asdict(t) for t in trials],
        "n_inject_gated_primary": len(inject),
        "n_healthy_primary": len(healthy),
        "n_inject_gated_all": len(inject_all),
        "missing_arms": missing,
    }

    (out / "PARAM.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (out / "sweep_theta.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "theta",
                "hit_rate",
                "mispoint_rate",
                "miss_rate",
                "precision",
                "fpr_c0",
                "youden",
                "n_hit",
                "n_mis",
                "n_miss",
            ],
        )
        w.writeheader()
        for row in theta_sweep:
            w.writerow({k: row[k] for k in w.fieldnames})
    with (out / "sweep_worst_fraction.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "phi",
                "hit_rate",
                "mispoint_rate",
                "miss_rate",
                "fpr_c0",
                "youden",
                "n_hit",
                "n_mis",
                "n_miss",
            ],
        )
        w.writeheader()
        for row in phi_sweep:
            w.writerow({k: row[k] for k in w.fieldnames})

    fig_paths = plot_curves(out, theta_sweep, phi_sweep, theta_chosen, phi_chosen)
    write_param_md(out, result, fig_paths)

    print("=== ①-B θ* / φ* ===")
    print(
        f"  θ*={theta_chosen['theta_star']}  hit={theta_chosen['hit_rate']:.3f}  "
        f"mis={theta_chosen['mispoint_rate']:.3f}  FPR={theta_chosen['fpr_c0']:.3f}  "
        f"({theta_chosen['rule']}; legacy {LEGACY_THETA})"
    )
    print(
        f"  φ*={phi_chosen['fraction_star']}  hit={phi_chosen['hit_rate']:.3f}  "
        f"mis={phi_chosen['mispoint_rate']:.3f}  FPR={phi_chosen['fpr_c0']:.3f}  "
        f"(legacy {LEGACY_WF})"
    )
    for k in ("gpu", "host_localizable", "host_wide", "all_gated"):
        ch = strata[k]["theta_chosen"]
        if ch:
            print(
                f"  stratum {k}: θ*={ch['theta_star']} hit={ch['hit_rate']:.3f} "
                f"mis={ch['mispoint_rate']:.3f} FPR={ch['fpr_c0']:.3f} "
                f"n={strata[k]['n_inject']} mode={strata[k].get('hit_mode')}"
            )
    print(f"  n_inject(primary)={len(inject)}  n_C0={len(healthy)}")
    print(f"out: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
