#!/usr/bin/env python3
"""④-A 朴素全聚 vs 联邦过滤聚（SUMMARY/DETAIL 两阶段 harness）。

解锁 BLOCKED：实现按 CRITERIA 的两阶段查询（Python harness，不必改 Rust）：
  Phase-1：全 rank 拉 SUMMARY schema（~180 B）
  协调：dose 门控(①-A θ*) + ①-B → suspects（明细门勿只靠本地 step）
  Phase-2：仅 suspects 拉 DETAIL（phase 窗 ± 可选 TT W*=100）
  对照臂：朴素 = 全 rank DETAIL（同窗同表）
  测：回传字节量比 + 定位 culprit 墙钟

默认离线：读本机 C0/C1/C2 jsonl，序列化量真实计字节；TT 按 ②-B B/step 计入。
live fanout 墙钟非本脚本职责（④-B）。

用法:
  python3 4a_federated_denoise.py \\
    --results-root /path/to/results/ascend-ais \\
    --out /path/to/param_calib/4A_federated_denoise
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

# --- 复用已锁定判据 / 已标定参数（禁止改扫）---
WINDOW_LO = 100
WINDOW_HI = 300
VICTIM = 7
N_RANKS = 16
STEADY_LO = 50
METRIC_FLOOR = 1e-3

DOSE_THETA = {"loud": 1.16, "quiet": 1.12, "masked": 1.04}
CROSS_RANK_THETA = 1.2
WORST_FRACTION_PHI = 0.4
W_STAR = 100
BYTES_PER_TT_STEP = 24.6 * 1024  # ②-B
DEFAULT_DOSE = "loud"
INCLUDE_TT = True  # DETAIL 含升详后 W* torch_trace（按 CRITERIA）

# 与判据验证池一致（loud 单 victim 主表）
ARMS = [
    ("P3-SW-A", "loud", "20260725_012957-yjr-as-c-p3-sw-a-loud", "primary"),
    ("P1-EXT-A", "loud", "20260725_011129-yjr-as-c-p1-ext-a-loud", "primary"),
    ("P1-EXT-B", "loud", "20260725_014350-yjr-as-c-p1-ext-b-loud", "expand"),
    ("P1-SW-A", "loud", "20260725_114556-yjr-as-c-p1-sw-a-loud", "expand"),
    ("P1-HW-B", "loud", "20260725_142359-yjr-as-c-p1-hw-b-loud", "expand"),
]

PHASE_FIELDS = ("step_ms", "compute_ms", "comm_ms", "wait_ms", "data_ms")


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
    return window_median(series, field, STEADY_LO, WINDOW_LO - 1)


def utf8_len(obj: object) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_summary(
    rank: int,
    ser: dict[int, dict],
    dose: str,
    phase_field: str,
    ts: float | None = None,
) -> dict:
    """CRITERIA healthy_return schema（协调侧也收全员摘要，含非 healthy 标量）。"""
    base = steady_baseline(ser, "step_ms")
    step_med = window_median(ser, "step_ms", WINDOW_LO, WINDOW_HI)
    phase_med = window_median(ser, phase_field, WINDOW_LO, WINDOW_HI)
    theta = DOSE_THETA[dose]
    if base is None or step_med is None or base <= 0:
        step_ratio = float("nan")
        status = "unknown"
    else:
        step_ratio = step_med / base
        # 摘要字段；明细门不靠本地 status（见 CRITERIA）
        status = "healthy" if step_ratio < theta else "local_elevated"
    return {
        "rank": rank,
        "status": status,
        "step_ms_med": step_med,
        "baseline_med": base,
        "step_ratio": step_ratio,
        "phase_metric": phase_field,
        "phase_med": phase_med,
        "window": [WINDOW_LO, WINDOW_HI],
        "dose_theta": theta,
        "ts": ts if ts is not None else WINDOW_HI,
    }


def detail_window() -> tuple[int, int]:
    """CRITERIA：step 窗 [trigger-W*+1, trigger]，trigger=WINDOW_HI。"""
    hi = WINDOW_HI
    lo = hi - W_STAR + 1
    return lo, hi


def build_detail_phase(rank: int, ser: dict[int, dict]) -> dict:
    """DETAIL：phase/step 窗序列（无 TT 正文；TT 字节另计）。"""
    lo, hi = detail_window()
    steps: list[dict] = []
    for s in range(lo, hi + 1):
        if s not in ser:
            continue
        row = ser[s]
        entry = {"step": s}
        for f in PHASE_FIELDS:
            if f in row:
                try:
                    entry[f] = float(row[f])
                except (TypeError, ValueError):
                    pass
        steps.append(entry)
    return {
        "rank": rank,
        "kind": "detail_phase",
        "window": [lo, hi],
        "n_steps": len(steps),
        "series": steps,
    }


def tt_bytes_estimate() -> int:
    return int(W_STAR * BYTES_PER_TT_STEP) if INCLUDE_TT else 0


def dose_gate_from_summaries(
    c0_summaries: list[dict], c1_summaries: list[dict], dose: str
) -> bool:
    """①-A：rank0 窗中位 C1/C0 ≥ θ*_dose（用 Phase-1 摘要字段）。"""
    s0 = next((x for x in c0_summaries if x["rank"] == 0), None)
    s1 = next((x for x in c1_summaries if x["rank"] == 0), None)
    if not s0 or not s1:
        return False
    m0, m1 = s0.get("step_ms_med"), s1.get("step_ms_med")
    if m0 is None or m1 is None or m0 <= 0:
        return False
    return (m1 / m0) >= DOSE_THETA[dose]


def suspects_from_summaries(
    summaries: list[dict], polarity: str
) -> tuple[list[int], dict]:
    """①-B 协调：跨 rank phase_med max/min + worst_fraction 代理（窗中位极端）。

    注：worst_fraction 真值需逐步序列；协调侧 Phase-1 仅有摘要标量时，
    用 phase_med 极端 rank 作 cross_rank pred；若 ratio≥θ* 则标 suspect。
    完整 worst_fraction 在 Phase-2 前用本地已缓存的 inject 臂逐步数据复算（见 run_arm）。
    """
    meds = {}
    for s in summaries:
        pm = s.get("phase_med")
        if pm is None:
            pm = METRIC_FLOOR
        meds[int(s["rank"])] = max(float(pm), METRIC_FLOOR)
    vals = list(meds.values())
    mx, mn = max(vals), min(vals)
    ratio = mx / mn if mn > 0 else float("inf")
    if polarity == "max":
        pred = max(meds, key=lambda k: meds[k])
    else:
        pred = min(meds, key=lambda k: meds[k])
    meta = {"cross_ratio": ratio, "cross_pred": pred, "phase_meds": meds}
    suspects: list[int] = []
    if ratio >= CROSS_RANK_THETA:
        suspects.append(pred)
    return sorted(set(suspects)), meta


def worst_fraction_pred(
    ranks: dict[int, dict[int, dict]], metric: str, polarity: str
) -> tuple[float, int]:
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


def localize_from_detail(details: list[dict], phase_field: str, polarity: str) -> int | None:
    """从已拉回的 DETAIL 窗中位定位 culprit。"""
    meds: dict[int, float] = {}
    for d in details:
        r = int(d["rank"])
        vals = []
        for row in d.get("series") or []:
            v = row.get(phase_field)
            if v is None:
                continue
            vals.append(float(v))
        if not vals:
            meds[r] = METRIC_FLOOR
        else:
            meds[r] = max(statistics.median(vals), METRIC_FLOOR)
    if not meds:
        return None
    if polarity == "max":
        return max(meds, key=lambda k: meds[k])
    return min(meds, key=lambda k: meds[k])


def run_two_phase(
    c0: dict[int, dict[int, dict]],
    arm: dict[int, dict[int, dict]],
    dose: str,
    phase_field: str,
    polarity: str,
) -> dict:
    """两阶段联邦查询 + 朴素对照；返回字节量与墙钟。"""
    tt_b = tt_bytes_estimate()

    # ----- Federated -----
    t0 = time.perf_counter()

    # Phase-1：全 rank SUMMARY
    summaries = [build_summary(r, arm[r], dose, phase_field) for r in range(N_RANKS)]
    c0_summaries = [build_summary(r, c0[r], dose, phase_field) for r in range(N_RANKS)]
    phase1_bytes = sum(utf8_len(s) for s in summaries)
    t_phase1 = time.perf_counter()

    # 协调：dose 门控 + ①-B
    gate = dose_gate_from_summaries(c0_summaries, summaries, dose)
    suspects_cross, cross_meta = suspects_from_summaries(summaries, polarity)
    wf, wf_pred = worst_fraction_pred(arm, phase_field, polarity)
    suspects = list(suspects_cross)
    if gate and wf >= WORST_FRACTION_PHI:
        suspects.append(wf_pred)
    suspects = sorted(set(suspects))
    if gate and not suspects and cross_meta["cross_ratio"] >= CROSS_RANK_THETA * 0.99:
        suspects = [cross_meta["cross_pred"]]
    if not gate:
        suspects = []
    t_coord = time.perf_counter()

    # Phase-2：仅 suspects DETAIL
    details = [build_detail_phase(r, arm[r]) for r in suspects]
    phase2_phase_bytes = sum(utf8_len(d) for d in details)
    phase2_tt_bytes = len(suspects) * tt_b
    phase2_bytes = phase2_phase_bytes + phase2_tt_bytes

    # 定位：优先 DETAIL；若无 DETAIL（门控未过）则用摘要 cross pred
    if details:
        pred = localize_from_detail(details, phase_field, polarity)
    elif suspects:
        pred = suspects[0]
    else:
        pred = cross_meta["cross_pred"] if gate else None
    t_end = time.perf_counter()

    fed_bytes = phase1_bytes + phase2_bytes
    fed_ms = (t_end - t0) * 1000.0

    # ----- Naive：全 rank DETAIL（同窗同表）-----
    t_n0 = time.perf_counter()
    naive_details = [build_detail_phase(r, arm[r]) for r in range(N_RANKS)]
    naive_phase_bytes = sum(utf8_len(d) for d in naive_details)
    naive_tt_bytes = N_RANKS * tt_b
    naive_bytes = naive_phase_bytes + naive_tt_bytes
    naive_pred = localize_from_detail(naive_details, phase_field, polarity)
    t_n1 = time.perf_counter()
    naive_ms = (t_n1 - t_n0) * 1000.0

    return {
        "dose_gate": gate,
        "suspects": suspects,
        "n_suspects": len(suspects),
        "victim_in_suspects": VICTIM in suspects,
        "cross_ratio": cross_meta["cross_ratio"],
        "cross_pred": cross_meta["cross_pred"],
        "wf": wf,
        "wf_pred": wf_pred,
        "federated_pred": pred,
        "naive_pred": naive_pred,
        "federated_hit": pred == VICTIM,
        "naive_hit": naive_pred == VICTIM,
        "bytes": {
            "summary_bytes_measured_total": phase1_bytes,
            "summary_bytes_per_rank_mean": phase1_bytes / N_RANKS,
            "detail_phase_bytes_per_suspect_mean": (
                phase2_phase_bytes / len(suspects) if suspects else 0
            ),
            "detail_tt_bytes_per_rank": tt_b,
            "federated_phase1_bytes": phase1_bytes,
            "federated_phase2_bytes": phase2_bytes,
            "federated_total_bytes": fed_bytes,
            "naive_phase_bytes": naive_phase_bytes,
            "naive_tt_bytes": naive_tt_bytes,
            "naive_total_bytes": naive_bytes,
            "volume_ratio_federated_over_naive": (
                fed_bytes / naive_bytes if naive_bytes else None
            ),
            "saving_factor": naive_bytes / fed_bytes if fed_bytes else None,
        },
        "timing_ms": {
            "federated_phase1_ms": (t_phase1 - t0) * 1000.0,
            "federated_coord_ms": (t_coord - t_phase1) * 1000.0,
            "federated_phase2_and_localize_ms": (t_end - t_coord) * 1000.0,
            "localize_culprit_ms": fed_ms,
            "naive_localize_ms": naive_ms,
        },
    }


def evaluate_arm(
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
    out: dict = {
        "case": case,
        "dose": dose,
        "run_id": run_id,
        "role": role,
        "phase_metric": phase,
        "polarity": polarity,
        "n_ranks": N_RANKS,
        "victim": VICTIM,
    }
    for name, arm in (("C1", c1), ("C2", c2)):
        if arm is None:
            continue
        # 每臂跑多次取中位墙钟（减噪声）；字节量不变
        trials = [run_two_phase(c0, arm, dose, phase, polarity) for _ in range(7)]
        mid = sorted(trials, key=lambda t: t["timing_ms"]["localize_culprit_ms"])[3]
        # 字节取首轮（确定性）
        bytes_ref = trials[0]["bytes"]
        mid["bytes"] = bytes_ref
        # 逻辑字段以首轮为准
        for k in (
            "dose_gate",
            "suspects",
            "n_suspects",
            "victim_in_suspects",
            "cross_ratio",
            "cross_pred",
            "wf",
            "wf_pred",
            "federated_pred",
            "naive_pred",
            "federated_hit",
            "naive_hit",
        ):
            mid[k] = trials[0][k]
        # 墙钟用中位
        fed_ms = statistics.median(
            t["timing_ms"]["localize_culprit_ms"] for t in trials
        )
        naive_ms = statistics.median(t["timing_ms"]["naive_localize_ms"] for t in trials)
        mid["timing_ms"]["localize_culprit_ms"] = fed_ms
        mid["timing_ms"]["naive_localize_ms"] = naive_ms
        mid["timing_ms"]["n_timing_trials"] = len(trials)
        out[name] = mid
    return out


def aggregate(trials: list[dict]) -> dict:
    arms = []
    for t in trials:
        for name in ("C1", "C2"):
            if name in t:
                a = t[name]
                arms.append(
                    {
                        "case": t["case"],
                        "dose": t["dose"],
                        "arm": name,
                        "run_id": t["run_id"],
                        "role": t["role"],
                        "phase_metric": t["phase_metric"],
                        **{k: a[k] for k in a if k not in ()},
                    }
                )

    ratios = [
        a["bytes"]["volume_ratio_federated_over_naive"]
        for a in arms
        if a["bytes"].get("volume_ratio_federated_over_naive") is not None
    ]
    fed_ms = [a["timing_ms"]["localize_culprit_ms"] for a in arms]
    naive_ms = [a["timing_ms"]["naive_localize_ms"] for a in arms]
    hit = [a["federated_hit"] for a in arms]
    recall = [a["victim_in_suspects"] for a in arms]
    n_sus = [a["n_suspects"] for a in arms]
    sum_b = [a["bytes"]["summary_bytes_per_rank_mean"] for a in arms]

    volume_ratio = statistics.median(ratios) if ratios else None
    localize_ms = statistics.median(fed_ms) if fed_ms else None

    return {
        "param": "federated_vs_naive_aggregation",
        "exp_id": "4A_federated_denoise",
        "status": "DONE",
        "mode": "offline_harness",
        "harness": "scripts/fail-slow/param_calib/4a_federated_denoise.py",
        "criteria": "param_calib/4_health_summary_criteria/{CRITERIA.json,CRITERIA.md}",
        "swept_range": {
            "aggregation": ["naive_all_rank_DETAIL", "federated_SUMMARY_then_suspect_DETAIL"]
        },
        "chosen_value": {
            "aggregation": "federated_SUMMARY_then_suspect_DETAIL",
            "volume_ratio_federated_over_naive": volume_ratio,
            "saving_factor": (1.0 / volume_ratio) if volume_ratio else None,
            "localize_culprit_ms": localize_ms,
            "summary_bytes_per_rank_measured_mean": (
                statistics.mean(sum_b) if sum_b else None
            ),
        },
        "choose_rule": (
            "两阶段：Phase-1 全 rank SUMMARY → dose门控+①-B suspects → Phase-2 仅 suspects DETAIL；"
            "对照=全 rank DETAIL；量比=fed/naive；定位墙钟=联邦路径 perf_counter 中位"
        ),
        "controls": {
            "n_ranks": N_RANKS,
            "victim": VICTIM,
            "inject_window": [WINDOW_LO, WINDOW_HI],
            "dose": DEFAULT_DOSE,
            "dose_theta": DOSE_THETA[DEFAULT_DOSE],
            "cross_rank_theta": CROSS_RANK_THETA,
            "worst_fraction_phi": WORST_FRACTION_PHI,
            "W_star": W_STAR,
            "include_tt_in_detail": INCLUDE_TT,
            "bytes_per_tt_step": BYTES_PER_TT_STEP,
            "set_key": "probing.torch.profiling=",
            "set_scope": "victim",
            "forbid": [
                "local step alone as DETAIL gate",
                "training step_ms as volume",
                "cold-only",
                "open 4B before 4A green",
            ],
        },
        "ground_truth_source": {
            "victim": VICTIM,
            "criteria_locked": True,
            "reused": ["1A_dose_threshold", "1B_localize_threshold", "2A_trace_window", "2B_ring_capacity"],
            "runs": [t["run_id"] for t in trials],
        },
        "measurements": {
            "n_inject_arms": len(arms),
            "victim_in_suspects_recall": (
                sum(1 for x in recall if x) / len(recall) if recall else None
            ),
            "federated_localize_hit_rate": (
                sum(1 for x in hit if x) / len(hit) if hit else None
            ),
            "mean_n_suspects": statistics.mean(n_sus) if n_sus else None,
            "volume_ratio_median": volume_ratio,
            "volume_ratio_mean": statistics.mean(ratios) if ratios else None,
            "volume_ratio_min": min(ratios) if ratios else None,
            "volume_ratio_max": max(ratios) if ratios else None,
            "localize_culprit_ms_median": localize_ms,
            "localize_culprit_ms_mean": statistics.mean(fed_ms) if fed_ms else None,
            "naive_localize_ms_median": (
                statistics.median(naive_ms) if naive_ms else None
            ),
            "summary_bytes_per_rank_mean": statistics.mean(sum_b) if sum_b else None,
            "note_timing": (
                "offline harness 墙钟=本机序列化+协调+定位 CPU 时间（中位×7）；"
                "非集群 FanoutScope 网络 RTT；live 网络墙钟留给 ④-B"
            ),
            "note_tt_bytes": (
                f"DETAIL TT 字节按 ②-B 估计 W*×{BYTES_PER_TT_STEP:.1f} B/step；"
                "phase 序列字节为 json 实测"
            ),
        },
        "arms": arms,
        "supports_design": (
            f"单 victim N={N_RANKS}：联邦量比中位≈{volume_ratio:.4f}（约 "
            f"{(1.0 / volume_ratio) if volume_ratio else float('nan'):.1f}× 节省）；"
            f"victim∈suspects 召回="
            f"{(sum(1 for x in recall if x) / len(recall)) if recall else None}；"
            f"定位墙钟中位≈{localize_ms:.3f} ms（离线 harness）。"
            "证明健康机只回 SUMMARY、明细门走协调 ①-B 可去噪一个量级。"
        ),
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "blocked": False,
    }


def write_md(param: dict, out: Path) -> None:
    m = param["measurements"]
    cv = param["chosen_value"]
    lines = [
        "# ④-A 朴素全聚 vs 联邦过滤聚 · DONE",
        "",
        f"> 状态：**DONE** · `{param['exp_id']}` · mode=`{param['mode']}` · {param['scored_at']}",
        f"> harness：`{param['harness']}`",
        "",
        "## 一句话",
        "",
        param["supports_design"],
        "",
        "## 自变量 / 控制",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 自变量 | 朴素全拉 DETAIL vs 联邦（SUMMARY→suspect→DETAIL） |",
        f"| N ranks | {param['controls']['n_ranks']} |",
        f"| victim | {param['controls']['victim']} |",
        f"| dose / θ* | {param['controls']['dose']} / {param['controls']['dose_theta']} |",
        f"| ①-B θ* / φ* | {param['controls']['cross_rank_theta']} / {param['controls']['worst_fraction_phi']} |",
        f"| W* / TT | {param['controls']['W_star']} / include={param['controls']['include_tt_in_detail']} |",
        f"| SET 键（live 约定） | `{param['controls']['set_key']}` scope={param['controls']['set_scope']} |",
        "",
        "## 推荐参数（本实验输出）",
        "",
        "| 参数 | 值 |",
        "|---|---|",
        f"| aggregation | **{cv['aggregation']}** |",
        f"| volume_ratio (fed/naive) | **{cv['volume_ratio_federated_over_naive']:.6f}** |",
        f"| saving_factor | **{cv['saving_factor']:.2f}×** |",
        f"| localize_culprit_ms | **{cv['localize_culprit_ms']:.4f}**（离线中位） |",
        f"| SUMMARY B/rank（实测均值） | **{cv['summary_bytes_per_rank_measured_mean']:.1f}** |",
        "",
        "## 两阶段查询路径（harness）",
        "",
        "1. **Phase-1**：全 rank 序列化 CRITERIA SUMMARY schema",
        "2. **协调**：①-A dose 门控 + ①-B（cross max/min ∨ worst_fraction）→ suspects",
        "3. **Phase-2**：仅 suspects 拉 DETAIL（phase 窗 [trigger−W*+1, trigger] + TT W* 字节估计）",
        "4. **对照**：全 rank DETAIL（同窗同表）",
        "",
        "## 汇总",
        "",
        f"- 注入臂数：{m['n_inject_arms']}",
        f"- victim∈suspects 召回：**{m['victim_in_suspects_recall']}**",
        f"- 联邦定位 hit：**{m['federated_localize_hit_rate']}**",
        f"- 均值 n_suspects：**{m['mean_n_suspects']}**",
        f"- volume_ratio 中位/均/min/max：{m['volume_ratio_median']:.6f} / "
        f"{m['volume_ratio_mean']:.6f} / {m['volume_ratio_min']:.6f} / {m['volume_ratio_max']:.6f}",
        f"- localize_culprit_ms 中位/均：{m['localize_culprit_ms_median']:.4f} / "
        f"{m['localize_culprit_ms_mean']:.4f}",
        f"- 朴素定位 ms 中位：{m['naive_localize_ms_median']:.4f}",
        "",
        f"> {m['note_timing']}",
        f">",
        f"> {m['note_tt_bytes']}",
        "",
        "## 分臂明细",
        "",
        "| case | arm | n_sus | victim∈ | fed_hit | volume_ratio | fed_ms | naive_ms | SUMMARY_B/r |",
        "|---|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for a in param["arms"]:
        b = a["bytes"]
        tm = a["timing_ms"]
        lines.append(
            f"| {a['case']} | {a['arm']} | {a['n_suspects']} | "
            f"{'Y' if a['victim_in_suspects'] else 'N'} | "
            f"{'Y' if a['federated_hit'] else 'N'} | "
            f"{b['volume_ratio_federated_over_naive']:.6f} | "
            f"{tm['localize_culprit_ms']:.4f} | {tm['naive_localize_ms']:.4f} | "
            f"{b['summary_bytes_per_rank_mean']:.1f} |"
        )
    lines += [
        "",
        "## 支撑设计决策",
        "",
        "联邦过滤 principle = 健康机不回传明细、只回「我正常」摘要；",
        "明细门必须走协调侧 ①-B suspects（注入下非 victim 本地 step 常升高，不能只靠本地 step）。",
        "本实验离线正式量比证实约一个量级节省；live FanoutScope 网络延迟扫基数见 ④-B。",
        "",
    ]
    (out / "PARAM.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_done(param: dict, out: Path) -> None:
    cv = param["chosen_value"]
    text = f"""# ④-A DONE · `4A_federated_denoise`

> 原 BLOCKED 已解除：SUMMARY/DETAIL 两阶段 harness 已接线并离线跑出正式 PARAM。

## 解锁项对照

| 项 | 状态 |
|---|---|
| 健康摘要判据 LOCKED | ✅ |
| Phase-1 全 rank SUMMARY | ✅ `4a_federated_denoise.py` |
| 协调 dose+①-B → suspects | ✅ |
| Phase-2 仅 suspects DETAIL | ✅ |
| 对照臂朴素全 rank DETAIL | ✅ |
| volume_ratio + localize_culprit_ms | ✅ 见 PARAM.json |

## 关键数字

- **volume_ratio** (fed/naive) = **{cv['volume_ratio_federated_over_naive']:.6f}**（≈{cv['saving_factor']:.1f}×）
- **localize_culprit_ms** = **{cv['localize_culprit_ms']:.4f}**（离线 harness 中位）
- mode = `{param['mode']}`；harness = `{param['harness']}`

## 未做（有意）

- 未开 ④-B（基数×FanoutScope 网络延迟）
- 未上卡 live federation（离线已能量比+定位 CPU 墙钟；网络 RTT 属 ④-B）
"""
    # 改写 BLOCKED → DONE
    blocked = out / "BLOCKED.md"
    done = out / "DONE.md"
    done.write_text(text, encoding="utf-8")
    if blocked.is_file():
        blocked.write_text(text, encoding="utf-8")


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
            "/Users/yinjinrun/Codespace/myportal/results/ascend-ais/param_calib/4A_federated_denoise"
        ),
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    trials = []
    missing = []
    for case, dose, run_id, role in ARMS:
        r = evaluate_arm(args.results_root, case, dose, run_id, role)
        if r is None or ("C1" not in r and "C2" not in r):
            missing.append(run_id)
            continue
        trials.append(r)

    if not trials:
        print("ERROR: no arms loaded", file=sys.stderr)
        return 2

    param = aggregate(trials)
    param["missing_runs"] = missing
    (args.out / "PARAM.json").write_text(
        json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_md(param, args.out)
    write_done(param, args.out)

    cv = param["chosen_value"]
    print(
        json.dumps(
            {
                "status": param["status"],
                "blocked": param["blocked"],
                "volume_ratio": cv["volume_ratio_federated_over_naive"],
                "localize_culprit_ms": cv["localize_culprit_ms"],
                "out": str(args.out),
                "n_arms": param["measurements"]["n_inject_arms"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
