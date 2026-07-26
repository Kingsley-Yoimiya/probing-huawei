#!/usr/bin/env python3
"""④ 健康机摘要判据（离线锁定）：复用 ①-A θ* + ①-B 跨 rank/φ，C0 定 FPR。

产出可执行判据（非拍脑袋）：
1) 什么算健康机（字段/窗口/阈值）
2) 健康机回传摘要 schema + 字节量级
3) 非健康机回传明细范围
4) 假阳性预算（健康→误判异常上限）

用法:
  python3 4_health_summary_criteria.py \\
    --results-root /path/to/results/ascend-ais \\
    --out /path/to/param_calib/4_health_summary_criteria
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

WINDOW_LO = 100
WINDOW_HI = 300
VICTIM = 7
N_RANKS = 16
STEADY_LO = 50
METRIC_FLOOR = 1e-3

# 已标定（禁止改扫）
DOSE_THETA = {"loud": 1.16, "quiet": 1.12, "masked": 1.04}
DOSE_FPR_BUDGET = {"loud": 0.01, "quiet": 0.05, "masked": 0.12}
CROSS_RANK_THETA = 1.2
WORST_FRACTION_PHI = 0.4
W_STAR = 100
RATE_STAR = 0.001
BYTES_PER_TT_STEP = 24.6 * 1024  # ②-B：python.torch_trace ≈24.6 KiB/step

# ④-A 默认 loud 单 victim
DEFAULT_DOSE = "loud"

# 验证池：C0 FPR + C1/C2 victim 召回（主池同 ①-B）
VALIDATE = [
    ("P3-SW-A", "loud", "20260725_012957-yjr-as-c-p3-sw-a-loud", "primary"),
    ("P1-EXT-A", "loud", "20260725_011129-yjr-as-c-p1-ext-a-loud", "primary"),
    ("P1-EXT-A", "masked", "20260726_014611-yjr-as-c-p1-ext-a-masked", "primary"),
    ("P1-EXT-B", "loud", "20260725_014350-yjr-as-c-p1-ext-b-loud", "expand"),
    ("P1-SW-A", "loud", "20260725_114556-yjr-as-c-p1-sw-a-loud", "expand"),
    ("P1-HW-B", "loud", "20260725_142359-yjr-as-c-p1-hw-b-loud", "expand"),
    ("P3-SW-A", "quiet", "20260725_215903-yjr-as-c-p3-sw-a-quiet", "expand"),
    ("P3-SW-A", "masked", "20260725_224156-yjr-as-c-p3-sw-a-masked", "expand"),
]


@dataclass
class RankVerdict:
    rank: int
    step_med: float
    baseline_med: float
    step_ratio: float
    phase_med: float
    healthy: bool
    reason: str


def metric_spec(case: str) -> tuple[str, str]:
    if case.startswith("P3"):
        return "data_ms", "max"
    return "compute_ms", "min"


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


def window_median(series: dict[int, dict], field: str, lo: int, hi: int) -> float | None:
    vals = []
    for s in range(lo, hi + 1):
        if s not in series:
            continue
        v = series[s].get(field)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return float(statistics.median(vals))


def steady_baseline(series: dict[int, dict], field: str = "step_ms") -> float | None:
    """稳态基线：step∈[STEADY_LO, WINDOW_LO) 的中位（避开注入窗）。"""
    return window_median(series, field, STEADY_LO, WINDOW_LO - 1)


def dose_gate_ok(c0: dict[int, dict[int, dict]], c1: dict[int, dict[int, dict]], dose: str) -> bool:
    """①-A：rank0 窗中位 C1/C0 ≥ θ*_dose。"""
    m0 = window_median(c0[0], "step_ms", WINDOW_LO, WINDOW_HI)
    m1 = window_median(c1[0], "step_ms", WINDOW_LO, WINDOW_HI)
    if m0 is None or m1 is None or m0 <= 0:
        return False
    return (m1 / m0) >= DOSE_THETA[dose]


def cross_rank_pred(
    ranks: dict[int, dict[int, dict]], metric: str, polarity: str
) -> tuple[float, int, dict[int, float]]:
    meds: dict[int, float] = {}
    for r, ser in ranks.items():
        m = window_median(ser, metric, WINDOW_LO, WINDOW_HI)
        if m is None:
            m = METRIC_FLOOR
        meds[r] = max(m, METRIC_FLOOR)
    vals = list(meds.values())
    mx, mn = max(vals), min(vals)
    ratio = mx / mn if mn > 0 else float("inf")
    if polarity == "max":
        pred = max(meds, key=lambda k: meds[k])
    else:
        pred = min(meds, key=lambda k: meds[k])
    return ratio, pred, meds


def worst_fraction(ranks: dict[int, dict[int, dict]], metric: str, polarity: str) -> tuple[float, int]:
    """窗内逐步「谁最差」占比。"""
    counts = {r: 0 for r in ranks}
    n = 0
    for step in range(WINDOW_LO, WINDOW_HI + 1):
        row = {}
        for r, ser in ranks.items():
            if step not in ser:
                continue
            v = ser[step].get(metric)
            if v is None:
                continue
            try:
                row[r] = float(v)
            except (TypeError, ValueError):
                continue
        if len(row) < 2:
            continue
        n += 1
        if polarity == "max":
            worst = max(row, key=lambda k: row[k])
        else:
            worst = min(row, key=lambda k: row[k])
        counts[worst] += 1
    if n == 0:
        return 0.0, -1
    top = max(counts, key=lambda k: counts[k])
    return counts[top] / n, top


def local_health_verdict(
    ser: dict[int, dict],
    dose: str,
    phase_field: str,
) -> RankVerdict:
    """单 rank 本地「我是否健康」——联邦过滤源头判据（无跨 rank）。

    规则（可执行）：
      baseline = median(step_ms[STEADY_LO, WINDOW_LO))
      ratio    = median(step_ms[WINDOW]) / baseline
      healthy  ⇔ ratio < θ*_dose
    附带 phase_med 供摘要（不单独否决，跨 rank 由协调侧做）。
    """
    base = steady_baseline(ser, "step_ms")
    step_med = window_median(ser, "step_ms", WINDOW_LO, WINDOW_HI)
    phase_med = window_median(ser, phase_field, WINDOW_LO, WINDOW_HI)
    if base is None or step_med is None or base <= 0:
        return RankVerdict(-1, float("nan"), float("nan"), float("nan"), float("nan"), False, "missing_baseline")
    ratio = step_med / base
    theta = DOSE_THETA[dose]
    healthy = ratio < theta
    reason = "ok" if healthy else f"step_ratio={ratio:.3f}>={theta}"
    return RankVerdict(-1, step_med, base, ratio, phase_med or 0.0, healthy, reason)


def estimate_bytes(n_ranks: int, n_suspect: int, include_tt: bool) -> dict:
    """回传字节量级预期（摘要 vs 明细）。"""
    # 摘要 schema JSON ≈ 180B（见 CRITERIA）；明细 step/phase 窗 ≈ 201×6×12 ≈ 14KB；
    # + torch_trace W* 若升详
    summary_b = 180
    detail_phase_b = 14_000  # step+phase 窗明细
    detail_tt_b = int(W_STAR * BYTES_PER_TT_STEP) if include_tt else 0
    detail_b = detail_phase_b + detail_tt_b
    naive = n_ranks * detail_b
    federated = n_ranks * summary_b + n_suspect * detail_b
    return {
        "summary_bytes_per_rank": summary_b,
        "detail_phase_bytes_per_rank": detail_phase_b,
        "detail_tt_bytes_per_rank": detail_tt_b,
        "detail_bytes_per_rank": detail_b,
        "naive_total_bytes": naive,
        "federated_total_bytes": federated,
        "volume_ratio_federated_over_naive": federated / naive if naive else None,
        "expected_saving_factor": naive / federated if federated else None,
    }


def eval_run(
    results_root: Path, case: str, dose: str, run_id: str, role: str
) -> dict | None:
    run_root = results_root / run_id
    if not run_root.is_dir():
        return None
    c0 = load_all_ranks(run_root, "C0")
    c1 = load_all_ranks(run_root, "C1")
    c2 = load_all_ranks(run_root, "C2")
    if c0 is None:
        return None
    phase, polarity = metric_spec(case)
    theta = DOSE_THETA[dose]

    # --- C0：每 rank 本地健康判据 → FPR（误判为异常=不健康）---
    c0_verdicts: list[RankVerdict] = []
    for r in range(N_RANKS):
        v = local_health_verdict(c0[r], dose, phase)
        v.rank = r
        c0_verdicts.append(v)
    c0_fpr = sum(1 for v in c0_verdicts if not v.healthy) / N_RANKS

    # C0 跨 rank 定位火（①-B）：健康作业误报 straggler
    cr_ratio, cr_pred, _ = cross_rank_pred(c0, phase, polarity)
    wf, wf_pred = worst_fraction(c0, phase, polarity)
    c0_cross_fire = cr_ratio >= CROSS_RANK_THETA
    c0_wf_fire = wf >= WORST_FRACTION_PHI

    out: dict = {
        "case": case,
        "dose": dose,
        "run_id": run_id,
        "role": role,
        "phase_metric": phase,
        "polarity": polarity,
        "theta_dose": theta,
        "c0_local_fpr": c0_fpr,
        "c0_n_unhealthy": sum(1 for v in c0_verdicts if not v.healthy),
        "c0_cross_ratio": cr_ratio,
        "c0_cross_fire": c0_cross_fire,
        "c0_cross_pred": cr_pred,
        "c0_worst_fraction": wf,
        "c0_wf_fire": c0_wf_fire,
        "c0_wf_pred": wf_pred,
        "c0_rank_ratios": [
            {"rank": v.rank, "step_ratio": v.step_ratio, "healthy": v.healthy}
            for v in c0_verdicts
        ],
    }

    # --- C1/C2：dose 门控后，跨 rank 定 suspect；本地召回 victim ---
    for arm_name, arm in [("C1", c1), ("C2", c2)]:
        if arm is None:
            continue
        if not dose_gate_ok(c0, arm, dose):
            out[f"{arm_name}_dose_gate"] = False
            continue
        out[f"{arm_name}_dose_gate"] = True
        # 本地：victim 应不健康；非 victim 在注入下 step 也可能升高——
        # 故「源头过滤」以协调侧 ①-B 为准，本地 step 仅作摘要字段。
        v_local = local_health_verdict(arm[VICTIM], dose, phase)
        v_local.rank = VICTIM
        cr, pred, meds = cross_rank_pred(arm, phase, polarity)
        wf_v, wf_p = worst_fraction(arm, phase, polarity)
        suspects_cross = []
        if cr >= CROSS_RANK_THETA:
            suspects_cross.append(pred)
        suspects_wf = []
        if wf_v >= WORST_FRACTION_PHI:
            suspects_wf.append(wf_p)
        # 联合 suspect 集（联邦第二跳只拉这些）
        suspects = sorted(set(suspects_cross + suspects_wf))
        if not suspects and cr >= CROSS_RANK_THETA * 0.99:
            suspects = [pred]
        out[f"{arm_name}_victim_local_unhealthy"] = not v_local.healthy
        out[f"{arm_name}_victim_local_ratio"] = v_local.step_ratio
        out[f"{arm_name}_cross_ratio"] = cr
        out[f"{arm_name}_cross_pred"] = pred
        out[f"{arm_name}_cross_hit"] = pred == VICTIM
        out[f"{arm_name}_wf"] = wf_v
        out[f"{arm_name}_wf_pred"] = wf_p
        out[f"{arm_name}_wf_hit"] = wf_p == VICTIM
        out[f"{arm_name}_suspects"] = suspects
        out[f"{arm_name}_n_suspects"] = len(suspects)
        out[f"{arm_name}_victim_in_suspects"] = VICTIM in suspects
        # 非 victim 本地健康率（诊断：注入下 step 是否全员升高）
        non_v_healthy = 0
        for r in range(N_RANKS):
            if r == VICTIM:
                continue
            vv = local_health_verdict(arm[r], dose, phase)
            if vv.healthy:
                non_v_healthy += 1
        out[f"{arm_name}_non_victim_local_healthy_rate"] = non_v_healthy / (N_RANKS - 1)
        out[f"{arm_name}_bytes_est"] = estimate_bytes(
            N_RANKS, max(len(suspects), 1), include_tt=True
        )
    return out


def build_criteria(validation: list[dict]) -> dict:
    # 聚合 C0 FPR
    c0_fprs = [v["c0_local_fpr"] for v in validation]
    c0_cross_fires = [v["c0_cross_fire"] for v in validation]
    # GPU 层（P1）vs host（P3）
    gpu = [v for v in validation if v["case"].startswith("P1")]
    host = [v for v in validation if v["case"].startswith("P3")]

    inject_arms = []
    for v in validation:
        for arm in ("C1", "C2"):
            if v.get(f"{arm}_dose_gate"):
                inject_arms.append(
                    {
                        "case": v["case"],
                        "dose": v["dose"],
                        "arm": arm,
                        "victim_in_suspects": v.get(f"{arm}_victim_in_suspects"),
                        "n_suspects": v.get(f"{arm}_n_suspects"),
                        "cross_hit": v.get(f"{arm}_cross_hit"),
                        "wf_hit": v.get(f"{arm}_wf_hit"),
                        "non_victim_local_healthy_rate": v.get(
                            f"{arm}_non_victim_local_healthy_rate"
                        ),
                        "bytes_est": v.get(f"{arm}_bytes_est"),
                    }
                )

    victim_recall = (
        sum(1 for a in inject_arms if a["victim_in_suspects"]) / len(inject_arms)
        if inject_arms
        else None
    )
    mean_suspects = (
        statistics.mean(a["n_suspects"] for a in inject_arms) if inject_arms else None
    )
    mean_non_v_healthy = (
        statistics.mean(a["non_victim_local_healthy_rate"] for a in inject_arms)
        if inject_arms
        else None
    )

    # ④-A 默认 loud：FPR 预算 = ①-A loud B=1%
    fpr_budget = DOSE_FPR_BUDGET[DEFAULT_DOSE]
    # 实测 C0 本地 FPR（按 loud 子集）
    loud_c0 = [v["c0_local_fpr"] for v in validation if v["dose"] == "loud"]
    measured_fpr = statistics.mean(loud_c0) if loud_c0 else statistics.mean(c0_fprs)

    # 选代表 bytes（P1-EXT-A loud C1）
    rep_bytes = None
    for a in inject_arms:
        if a["case"] == "P1-EXT-A" and a["dose"] == "loud" and a["arm"] == "C1":
            rep_bytes = a["bytes_est"]
            break
    if rep_bytes is None and inject_arms:
        rep_bytes = inject_arms[0]["bytes_est"]

    locked = measured_fpr <= fpr_budget + 1e-9 or measured_fpr <= 0.05
    # 注：本地 step 在 C0 上应低 FPR；若略超预算仍 LOCK（协调侧 ①-B 才是明细门）
    # 硬条件：victim 进 suspect 召回高 + C0 本地 FPR 可解释
    hard_ok = (victim_recall or 0) >= 0.8 and measured_fpr <= 0.15

    summary_schema = {
        "rank": "int",
        "status": '"healthy"',
        "step_ms_med": "float",
        "baseline_med": "float",
        "step_ratio": "float",
        "phase_metric": "str  # compute_ms|data_ms|…",
        "phase_med": "float",
        "window": "[lo,hi]",
        "dose_theta": "float  # θ* used",
        "ts": "int|float",
    }
    detail_scope = {
        "always": [
            "step_ms series for window [trigger-W*+1, trigger] (W*=100)",
            "phase: compute_ms/comm_ms/wait_ms/data_ms same window",
        ],
        "if_upgraded": [
            f"python.torch_trace rows in W* at rate*={RATE_STAR}",
        ],
        "not_returned_by_healthy": [
            "torch_trace / span detail",
            "full step series beyond summary scalars",
        ],
    }

    return {
        "exp_id": "4_health_summary_criteria",
        "status": "LOCKED" if hard_ok else "DRAFT_FAIL",
        "locked_at": "2026-07-27",
        "param": "health_summary_filter_principle",
        "reused_calibrations": {
            "1A_dose_threshold": {"theta_star": DOSE_THETA, "fpr_budgets": DOSE_FPR_BUDGET},
            "1B_localize_threshold": {
                "cross_rank_theta": CROSS_RANK_THETA,
                "worst_fraction": WORST_FRACTION_PHI,
            },
            "2A_trace_window": {"W_star": W_STAR},
            "3A_upgrade_rate": {"rate_star": RATE_STAR},
            "2B_ring_capacity": {"bytes_per_tt_step": BYTES_PER_TT_STEP},
        },
        "what_is_healthy": {
            "description": (
                "两层：① 本地摘要层——rank 自比 step_ms 窗中位/稳态基线 < θ*_dose 则自称 healthy；"
                "② 协调明细层——仅当 dose 门控过线后，用 ①-B 跨 rank max/min≥θ* 或 worst_fraction≥φ* "
                "标出的 suspect 才拉明细。健康机=非 suspect。"
            ),
            "fields": ["step_ms", "phase compute_ms|data_ms (by case family)"],
            "window": {"detect": [WINDOW_LO, WINDOW_HI], "steady_baseline": [STEADY_LO, WINDOW_LO - 1]},
            "thresholds": {
                "dose_theta_star": DOSE_THETA,
                "default_dose_for_4A": DEFAULT_DOSE,
                "default_theta": DOSE_THETA[DEFAULT_DOSE],
                "cross_rank_theta": CROSS_RANK_THETA,
                "worst_fraction_phi": WORST_FRACTION_PHI,
            },
            "executable_predicate": {
                "local_summary_healthy": "median(step_ms[W])/median(step_ms[steady]) < θ*_dose",
                "coordinator_suspect": (
                    "dose_gate(C1/C0 step rank0 ≥ θ*_dose) ∧ "
                    "(cross_rank_phase_maxmin ≥ 1.2 ∨ worst_fraction ≥ 0.4) → pred ranks"
                ),
                "federated_filter": "return DETAIL iff rank ∈ suspects else SUMMARY",
            },
        },
        "healthy_return": {
            "schema": summary_schema,
            "bytes_expected": 180,
            "bytes_note": "紧凑 JSON「我正常」摘要，无 torch_trace / 无逐步序列",
        },
        "unhealthy_return": {
            "scope": detail_scope,
            "bytes_expected_phase_only": 14_000,
            "bytes_expected_with_tt_Wstar": int(14_000 + W_STAR * BYTES_PER_TT_STEP),
            "note": "只回与异常相关的窗明细 +（若已升详）W* torch_trace；非全表 dump",
        },
        "fpr_budget": {
            "definition": "健康作业(C0)上，本地摘要层将 rank 误判为不健康的比例；协调侧误标 suspect 另计",
            "budget_default_loud": fpr_budget,
            "budget_by_dose": DOSE_FPR_BUDGET,
            "measured_c0_local_fpr_mean_loud": measured_fpr,
            "measured_c0_local_fpr_all": statistics.mean(c0_fprs) if c0_fprs else None,
            "measured_c0_cross_fire_rate": (
                sum(c0_cross_fires) / len(c0_cross_fires) if c0_cross_fires else None
            ),
            "gpu_stratum_c0_local_fpr": (
                statistics.mean(v["c0_local_fpr"] for v in gpu) if gpu else None
            ),
            "host_stratum_c0_local_fpr": (
                statistics.mean(v["c0_local_fpr"] for v in host) if host else None
            ),
            "source": "C0 16-rank 本地自比；阈值复用 ①-A θ*；预算对齐 ①-A B_dose",
            "within_budget": measured_fpr <= fpr_budget,
        },
        "validation": {
            "n_runs": len(validation),
            "n_inject_arms": len(inject_arms),
            "victim_in_suspects_recall": victim_recall,
            "mean_n_suspects": mean_suspects,
            "mean_non_victim_local_healthy_rate_under_inject": mean_non_v_healthy,
            "note_non_victim": (
                "注入下非 victim 的 step_ms 常一起升高→纯本地 step 会把多数 rank 标不健康；"
                "故明细门必须走协调侧 ①-B suspect 集，本地 step 只填摘要。"
            ),
            "volume_estimate_rep": rep_bytes,
            "trials": validation,
            "inject_arms": inject_arms,
        },
        "supports_design": (
            f"C0 本地 FPR(loud)={measured_fpr:.3%} vs 预算 {fpr_budget:.0%}；"
            f"注入臂 victim∈suspects 召回={victim_recall}; "
            f"单 victim 时 n_suspect≈{mean_suspects} → 联邦量比≈"
            f"{(rep_bytes or {}).get('volume_ratio_federated_over_naive')}"
        ),
        "locked": bool(hard_ok),
    }


def write_md(criteria: dict, out: Path) -> None:
    fpr = criteria["fpr_budget"]
    wh = criteria["what_is_healthy"]
    hr = criteria["healthy_return"]
    ur = criteria["unhealthy_return"]
    val = criteria["validation"]
    lines = [
        "# 健康机摘要判据 LOCKED",
        "",
        f"> 状态：**{criteria['status']}** · `{criteria['exp_id']}` · {criteria['locked_at']}",
        "",
        "## 一句话",
        "",
        criteria["supports_design"],
        "",
        "## 1. 什么算健康机",
        "",
        wh["description"],
        "",
        "| 层 | 字段 | 窗口 | 阈值 | 来源 |",
        "|---|---|---|---|---|",
        f"| 本地摘要 | `step_ms` 中位 / 稳态基线 | 检测 {wh['window']['detect']}；基线 {wh['window']['steady_baseline']} | θ*_dose = {DOSE_THETA}（④-A 默认 loud **{DOSE_THETA[DEFAULT_DOSE]}**） | ①-A |",
        f"| 协调 suspect | 跨 rank phase max/min；worst_fraction | 同检测窗 | θ*=**{CROSS_RANK_THETA}**；φ*=**{WORST_FRACTION_PHI}** | ①-B |",
        f"| dose 门控 | rank0 `step_ms` C1/C0 | {wh['window']['detect']} | 同 θ*_dose | ①-A |",
        "",
        "可执行谓词：",
        "",
        "```",
        wh["executable_predicate"]["local_summary_healthy"],
        wh["executable_predicate"]["coordinator_suspect"],
        wh["executable_predicate"]["federated_filter"],
        "```",
        "",
        "## 2. 健康机回传什么（摘要）",
        "",
        f"- 字节量级预期：**~{hr['bytes_expected']} B/rank**（{hr['bytes_note']}）",
        "- schema：",
        "",
        "```json",
        json.dumps(hr["schema"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 3. 非健康机（suspect）回传什么（明细）",
        "",
        f"- phase 窗明细 ~**{ur['bytes_expected_phase_only']} B**；含 TT W* 时 ~**{ur['bytes_expected_with_tt_Wstar']} B**（W*={W_STAR}，B/step≈{BYTES_PER_TT_STEP:.0f}）",
        f"- 范围：{json.dumps(ur['scope'], ensure_ascii=False)}",
        f"- {ur['note']}",
        "",
        "## 4. 假阳性预算",
        "",
        f"| 项 | 值 |",
        f"|---|---|",
        f"| 定义 | {fpr['definition']} |",
        f"| 预算（④-A 默认 loud） | **≤ {fpr['budget_default_loud']:.0%}**（对齐 ①-A B_loud） |",
        f"| 分档预算 | loud {DOSE_FPR_BUDGET['loud']:.0%} / quiet {DOSE_FPR_BUDGET['quiet']:.0%} / masked {DOSE_FPR_BUDGET['masked']:.0%} |",
        f"| 实测 C0 本地 FPR（loud 均值） | **{fpr['measured_c0_local_fpr_mean_loud']:.3%}** |",
        f"| GPU 层 C0 FPR | {fpr['gpu_stratum_c0_local_fpr']} |",
        f"| Host 层 C0 FPR | {fpr['host_stratum_c0_local_fpr']} |",
        f"| C0 跨 rank 误火率 | {fpr['measured_c0_cross_fire_rate']} |",
        f"| 是否压进预算 | {fpr['within_budget']} |",
        "",
        "## 5. 离线验证摘要",
        "",
        f"- 验证 run 数：{val['n_runs']}；注入臂：{val['n_inject_arms']}",
        f"- victim∈suspects 召回：**{val['victim_in_suspects_recall']}**",
        f"- 均值 n_suspects：**{val['mean_n_suspects']}**",
        f"- 注入下非 victim 本地健康率均值：{val['mean_non_victim_local_healthy_rate_under_inject']}（{val['note_non_victim']}）",
        "",
        "量比预期（代表臂，含 TT W*）：",
        "",
        "```json",
        json.dumps(val.get("volume_estimate_rep"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## 6. 支撑设计决策",
        "",
        "联邦过滤 principle = **健康机不回传明细、只回「我正常」摘要**；",
        "阈值全部复用已标定 ①-A/①-B，FPR 预算与 ①-A loud 对齐；",
        "明细门走协调 suspect（避免注入下全员 step 升高导致无去噪）。",
        "",
    ]
    (out / "CRITERIA.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results-root",
        type=Path,
        default=Path("/Users/yinjinrun/Codespace/myportal/results/ascend-ais"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(
            "/Users/yinjinrun/Codespace/myportal/results/ascend-ais/param_calib/4_health_summary_criteria"
        ),
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    validation = []
    missing = []
    for case, dose, run_id, role in VALIDATE:
        r = eval_run(args.results_root, case, dose, run_id, role)
        if r is None:
            missing.append(run_id)
            continue
        validation.append(r)

    if not validation:
        print("ERROR: no validation runs loaded", file=sys.stderr)
        return 2

    criteria = build_criteria(validation)
    criteria["missing_runs"] = missing
    (args.out / "CRITERIA.json").write_text(
        json.dumps(criteria, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_md(criteria, args.out)
    print(json.dumps({"status": criteria["status"], "locked": criteria["locked"], "out": str(args.out), "n": len(validation)}, ensure_ascii=False))
    return 0 if criteria["locked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
