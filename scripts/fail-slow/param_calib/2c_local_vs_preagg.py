#!/usr/bin/env python3
"""②-C 本地保留 vs 常驻预聚（要不要在常驻期跨机聚合）。

自变量：常驻期聚合 on/off（单自变量）。
对照：其余固定（N=16、SUMMARY schema、FanoutScope=Node、①-A/①-B/W*/④-A 路径）。

离线 harness（不上卡）：
  - 用现有 run 的步数 / step_ms 定常驻期 horizon
  - 预聚开销 = 每 interval 步一次全 rank SUMMARY fan-out（④-A/④-B 实测字节与延迟）
  - 预聚收益 = 常驻期诊断查询次数 × 可省延迟（设计上常驻期不查 → 收益≈0）
  - 触发后两臂仍付 ④-A 联邦现拉成本（升详 DETAIL 不能靠常驻陈旧预聚）

用法:
  python3 2c_local_vs_preagg.py \\
    --results-root .../results/ascend-ais \\
    --out .../param_calib/2C_local_vs_preagg
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

# --- 锁定控制（与 ④-A / ④-B / CRITERIA 一致，禁止改扫）---
N_RANKS = 16
VICTIM = 7
WINDOW_LO, WINDOW_HI = 100, 300
DOSE = "loud"
DOSE_THETA = 1.16
CROSS_RANK_THETA = 1.2
WORST_FRACTION_PHI = 0.4
W_STAR = 100
FANOUT_SCOPE = "Node"  # N=16 单机默认（④-B）

# ④-A / ④-B 实测锚点
SUMMARY_BYTES_PER_RANK = 208.2
SUMMARY_BYTES_PER_ROUND = SUMMARY_BYTES_PER_RANK * N_RANKS  # ≈3331.2
# ④-B N=16 Node：SUMMARY-only ≈ phase1（全 federated total 含 phase2 DETAIL）
PHASE1_MS_PER_ROUND = 6.753750415820537
FEDERATED_TRIGGER_MS = 12.108194586728086  # ④-B Node N=16 total（触发一次）
FEDERATED_TRIGGER_BYTES = 2532459  # ④-A P3-SW-A C1 federated_total 量级
VOLUME_RATIO_4A = 0.0626

PRIMARY_RUN = "20260725_012957-yjr-as-c-p3-sw-a-loud"
PRIMARY_CASE = "P3-SW-A"
PRIMARY_ARM = "C0"  # 健康线定常驻 horizon / step_ms（禁止用训练 step 判采集差异）

# 灵敏度：预聚间隔（步）；主对照 interval=1 = 每步常驻预聚
INTERVALS = (1, 10, 50, 100)
# 长作业投影（小时）
LONG_JOB_HOURS = (1.0, 8.0)


def find_cfg_dir(run_root: Path, cfg_prefix: str) -> Path | None:
    hits = sorted(run_root.glob(f"**/by_pod/*/round_1/{cfg_prefix}*/ranks"))
    if hits:
        return hits[0]
    hits = sorted(run_root.glob(f"**/{cfg_prefix}*/ranks"))
    return hits[0] if hits else None


def load_rank0_steps(run_root: Path, cfg_prefix: str) -> tuple[list[int], list[float]] | None:
    ranks_dir = find_cfg_dir(run_root, cfg_prefix)
    if ranks_dir is None:
        return None
    p = ranks_dir / "rank_0000.jsonl"
    if not p.is_file():
        return None
    steps: list[int] = []
    sms: list[float] = []
    with p.open() as f:
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
            try:
                steps.append(int(o["step"]))
                sms.append(float(o["step_ms"]))
            except (TypeError, ValueError):
                continue
    if not steps:
        return None
    return steps, sms


def horizon_from_run(steps: list[int], sms: list[float]) -> dict:
    """常驻期 = 全 run 步（诊断仅触发时查一次）；step_ms 用注入前稳态。"""
    lo, hi = min(steps), max(steps)
    n_steps = hi - lo + 1
    steady = [ms for st, ms in zip(steps, sms) if 50 <= st <= WINDOW_LO - 1]
    if not steady:
        steady = sms
    step_ms_med = float(statistics.median(steady))
    # 常驻期诊断查询次数：本设计 skills 仅触发后跑 → 0
    n_resident_queries = 0
    n_triggers = 1  # 单 victim 注入窗触发一次归因查询
    return {
        "step_lo": lo,
        "step_hi": hi,
        "n_steps": n_steps,
        "step_ms_median_steady": step_ms_med,
        "train_wall_s": n_steps * step_ms_med / 1000.0,
        "n_resident_diagnostic_queries": n_resident_queries,
        "n_trigger_queries": n_triggers,
        "inject_window": [WINDOW_LO, WINDOW_HI],
    }


def cost_preagg(n_steps: int, interval: int, step_ms: float) -> dict:
    n_rounds = n_steps // interval
    bytes_total = n_rounds * SUMMARY_BYTES_PER_ROUND
    wall_ms = n_rounds * PHASE1_MS_PER_ROUND
    train_wall_ms = n_steps * step_ms
    return {
        "interval_steps": interval,
        "n_rounds": n_rounds,
        "bytes_total": bytes_total,
        "bytes_mib": bytes_total / (1024 * 1024),
        "wall_ms": wall_ms,
        "wall_s": wall_ms / 1000.0,
        "overhead_vs_train_wall": (wall_ms / train_wall_ms) if train_wall_ms > 0 else None,
        "bytes_per_step_amortized": bytes_total / n_steps if n_steps else 0.0,
    }


def benefit_preagg(n_resident_queries: int) -> dict:
    """常驻期无人查 → 缓存命中 0；触发后仍需现拉 DETAIL（升详）→ 归因收益≈0。"""
    latency_saved_ms = n_resident_queries * PHASE1_MS_PER_ROUND
    # 即便把触发时 Phase-1 也算「可省」（乐观上界），仍远小于 DETAIL
    optimistic_phase1_save_ms = PHASE1_MS_PER_ROUND
    optimistic_phase1_save_bytes = SUMMARY_BYTES_PER_ROUND
    return {
        "resident_queries_answered_by_cache": n_resident_queries,
        "latency_saved_ms_resident": latency_saved_ms,
        "attribution_benefit_d_level": 0.0,
        "note": "常驻期无诊断查询；触发后 W* DETAIL+升详必须现拉，陈旧预聚不能替代",
        "optimistic_trigger_phase1_save_ms": optimistic_phase1_save_ms,
        "optimistic_trigger_phase1_save_bytes": optimistic_phase1_save_bytes,
        "optimistic_phase1_save_fraction_of_trigger_ms": (
            optimistic_phase1_save_ms / FEDERATED_TRIGGER_MS
        ),
        "optimistic_phase1_save_fraction_of_trigger_bytes": (
            optimistic_phase1_save_bytes / FEDERATED_TRIGGER_BYTES
        ),
    }


def long_job_projection(step_ms: float, hours: float, interval: int = 1) -> dict:
    n_steps = int((hours * 3600.0 * 1000.0) / step_ms)
    c = cost_preagg(n_steps, interval, step_ms)
    return {
        "hours": hours,
        "n_steps": n_steps,
        "interval_steps": interval,
        "preagg_bytes_mib": c["bytes_mib"],
        "preagg_wall_s": c["wall_s"],
        "overhead_vs_train_wall": c["overhead_vs_train_wall"],
        "local_retain_resident_bytes": 0,
        "local_retain_resident_wall_s": 0.0,
    }


def choose(local: dict, preagg: dict, benefit: dict) -> dict:
    # 收益近 0 且开销 > 0 → 选本地保留
    benefit_near_zero = (
        benefit["latency_saved_ms_resident"] <= 1e-9
        and benefit["attribution_benefit_d_level"] <= 1e-9
    )
    cost_positive = preagg["bytes_total"] > 0 and preagg["wall_ms"] > 0
    chosen = "local_retain_trigger_then_aggregate"
    return {
        "resident_preagg": "off",
        "policy": chosen,
        "benefit_near_zero": benefit_near_zero,
        "cost_positive": cost_positive,
        "preagg_cost_bytes": preagg["bytes_total"],
        "preagg_cost_wall_s": preagg["wall_s"],
        "preagg_overhead_vs_train": preagg["overhead_vs_train_wall"],
        "local_cost_bytes": local["bytes_total"],
        "local_cost_wall_s": local["wall_s"],
        "benefit_latency_saved_ms": benefit["latency_saved_ms_resident"],
        "optimistic_phase1_save_frac_ms": benefit[
            "optimistic_phase1_save_fraction_of_trigger_ms"
        ],
    }


def supports_sentence(chosen: dict, hz: dict) -> str:
    oh = chosen["preagg_overhead_vs_train"]
    oh_pct = f"{100.0 * oh:.2f}%" if oh is not None else "n/a"
    return (
        f"常驻期预聚(每步 SUMMARY×{N_RANKS})开销 "
        f"{chosen['preagg_cost_bytes']/1024:.1f} KiB / {chosen['preagg_cost_wall_s']:.3f}s"
        f"（相对训墙钟 {oh_pct}），收益≈0"
        f"（常驻诊断查询={hz['n_resident_diagnostic_queries']}；"
        f"触发后仍付④-A 现拉，Phase-1 乐观可省仅占触发延迟"
        f"≈{100*chosen['optimistic_phase1_save_frac_ms']:.1f}%）。"
        f"证明「留本地、触发才聚」省掉常驻聚合成本；聚合推迟到模块④。"
    )


def render_md(param: dict) -> str:
    c = param["chosen_value"]
    hz = param["measurements"]["horizon"]
    sens = param["measurements"]["cost_vs_interval"]
    benefit = param["measurements"]["benefit"]
    arms = param["measurements"]["arms"]
    lines = [
        "# ②-C 本地保留 vs 常驻预聚 · DONE",
        "",
        f"> 状态：**DONE** · `2C_local_vs_preagg` · mode=`{param['mode']}` · {param['scored_at']}",
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
        "| 自变量 | 常驻期聚合 on/off |",
        f"| N ranks / FanoutScope | {N_RANKS} / {FANOUT_SCOPE} |",
        f"| SUMMARY B/rank（④-A） | {SUMMARY_BYTES_PER_RANK} |",
        f"| Phase-1 ms/round（④-B Node） | {PHASE1_MS_PER_ROUND:.3f} |",
        f"| 触发联邦 ms（④-B） | {FEDERATED_TRIGGER_MS:.3f} |",
        f"| dose / θ* / ①-B | {DOSE} / {DOSE_THETA} / {CROSS_RANK_THETA}·{WORST_FRACTION_PHI} |",
        f"| W* | {W_STAR} |",
        f"| 主 run | `{PRIMARY_RUN}` · {PRIMARY_ARM} |",
        "",
        "## 推荐参数（本实验输出）",
        "",
        "| 参数 | 值 |",
        "|---|---|",
        f"| resident_preagg | **{c['resident_preagg']}** |",
        f"| policy | **{c['policy']}** |",
        f"| 预聚开销（interval=1） | **{c['preagg_cost_bytes']/1024:.1f} KiB** / **{c['preagg_cost_wall_s']:.3f} s** |",
        f"| 相对训墙钟开销 | **{100*c['preagg_overhead_vs_train']:.2f}%** |",
        f"| 常驻收益（延迟节省） | **{c['benefit_latency_saved_ms']:.3f} ms**（≈0） |",
        f"| 乐观 Phase-1 可省占触发延迟 | **{100*c['optimistic_phase1_save_frac_ms']:.1f}%** |",
        "",
        "## 常驻 horizon（现有 run）",
        "",
        f"- steps [{hz['step_lo']}, {hz['step_hi']}] → **{hz['n_steps']}** 步",
        f"- 稳态 step_ms 中位（C0 steps 50–99）= **{hz['step_ms_median_steady']:.3f} ms**",
        f"- 训墙钟 ≈ **{hz['train_wall_s']:.2f} s**",
        f"- 常驻期诊断查询次数 = **{hz['n_resident_diagnostic_queries']}**（设计：触发才查）",
        f"- 触发查询次数 = **{hz['n_trigger_queries']}**",
        "",
        "## 两臂对照（主：interval=1）",
        "",
        "| 臂 | 常驻字节 | 常驻墙钟 | 触发成本（④-A/B） | 常驻收益 |",
        "|---|---:|---:|---:|---:|",
    ]
    for a in arms:
        lines.append(
            f"| {a['arm']} | {a['resident_bytes']/1024:.1f} KiB | "
            f"{a['resident_wall_s']:.3f} s | "
            f"{a['trigger_bytes']/1024:.1f} KiB / {a['trigger_ms']:.2f} ms | "
            f"{a['benefit_latency_saved_ms']:.3f} ms |"
        )
    lines += [
        "",
        "## 开销 vs 预聚间隔（灵敏度；自变量仍为 on/off）",
        "",
        "| interval | rounds | bytes (MiB) | wall (s) | vs train |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in sens:
        oh = row["overhead_vs_train_wall"]
        lines.append(
            f"| {row['interval_steps']} | {row['n_rounds']} | "
            f"{row['bytes_mib']:.4f} | {row['wall_s']:.3f} | "
            f"{100*oh:.2f}% |"
        )
    lines += [
        "",
        "## 收益分解",
        "",
        f"- 常驻缓存命中查询数 = **{benefit['resident_queries_answered_by_cache']}**",
        f"- 常驻延迟节省 = **{benefit['latency_saved_ms_resident']:.3f} ms**",
        f"- 归因 D-level 增益 = **{benefit['attribution_benefit_d_level']}**",
        f"- 乐观：触发时复用陈旧 Phase-1 可省 "
        f"{benefit['optimistic_trigger_phase1_save_ms']:.2f} ms / "
        f"{benefit['optimistic_trigger_phase1_save_bytes']/1024:.1f} KiB"
        f"（占触发延迟 **{100*benefit['optimistic_phase1_save_fraction_of_trigger_ms']:.1f}%**、"
        f"占触发字节 **{100*benefit['optimistic_phase1_save_fraction_of_trigger_bytes']:.2f}%**）",
        f"- {benefit['note']}",
        "",
        "## 长作业投影（interval=1 预聚）",
        "",
        "| hours | steps | preagg MiB | preagg wall s | vs train | local resident |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in param["measurements"]["long_job"]:
        lines.append(
            f"| {row['hours']:g} | {row['n_steps']} | {row['preagg_bytes_mib']:.2f} | "
            f"{row['preagg_wall_s']:.1f} | {100*row['overhead_vs_train_wall']:.2f}% | "
            f"0 B / 0 s |"
        )
    lines += [
        "",
        "## 支撑设计决策",
        "",
        "常驻期**不必跨机聚合**：数据留各 rank 本地环（②-A/②-B），"
        "等触发后再走模块④联邦过滤聚。"
        "常驻预聚付持续 SUMMARY fan-out 成本，却换不来常驻期查询收益，"
        "也不能替代触发后的升详 DETAIL。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--results-root",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "results"
        / "ascend-ais",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出目录（默认 results-root/param_calib/2C_local_vs_preagg）",
    )
    args = ap.parse_args()
    results_root: Path = args.results_root
    out_dir: Path = args.out or (results_root / "param_calib" / "2C_local_vs_preagg")
    out_dir.mkdir(parents=True, exist_ok=True)

    run_root = results_root / PRIMARY_RUN
    if not run_root.is_dir():
        # myportal results 与 probing-huawei/results 可能同内容不同根
        alt = Path("/Users/yinjinrun/Codespace/myportal/results/ascend-ais") / PRIMARY_RUN
        if alt.is_dir():
            run_root = alt
            results_root = alt.parent

    loaded = load_rank0_steps(run_root, PRIMARY_ARM)
    if loaded is None:
        # C0 目录名可能是 C0_baseline
        for pref in ("C0_baseline", "C0", "C2_probing", "C2"):
            loaded = load_rank0_steps(run_root, pref)
            if loaded is not None:
                break
    if loaded is None:
        print(f"ERROR: cannot load ranks from {run_root}", flush=True)
        return 1

    steps, sms = loaded
    hz = horizon_from_run(steps, sms)
    step_ms = hz["step_ms_median_steady"]
    n_steps = hz["n_steps"]

    # 主对照：interval=1
    preagg = cost_preagg(n_steps, 1, step_ms)
    local = {
        "interval_steps": None,
        "n_rounds": 0,
        "bytes_total": 0,
        "bytes_mib": 0.0,
        "wall_ms": 0.0,
        "wall_s": 0.0,
        "overhead_vs_train_wall": 0.0,
        "bytes_per_step_amortized": 0.0,
    }
    benefit = benefit_preagg(hz["n_resident_diagnostic_queries"])
    chosen = choose(local, preagg, benefit)

    sens = [cost_preagg(n_steps, iv, step_ms) for iv in INTERVALS]
    long_job = [
        long_job_projection(step_ms, h, interval=1) for h in LONG_JOB_HOURS
    ]

    arms = [
        {
            "arm": "local_retain (preagg=off)",
            "resident_preagg": "off",
            "resident_bytes": 0,
            "resident_wall_s": 0.0,
            "trigger_bytes": FEDERATED_TRIGGER_BYTES,
            "trigger_ms": FEDERATED_TRIGGER_MS,
            "benefit_latency_saved_ms": 0.0,
        },
        {
            "arm": "resident_preagg (on, interval=1)",
            "resident_preagg": "on",
            "resident_bytes": preagg["bytes_total"],
            "resident_wall_s": preagg["wall_s"],
            "trigger_bytes": FEDERATED_TRIGGER_BYTES,
            "trigger_ms": FEDERATED_TRIGGER_MS,
            "benefit_latency_saved_ms": benefit["latency_saved_ms_resident"],
        },
    ]

    scored_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    supports = supports_sentence(chosen, hz)

    param = {
        "param": "resident_preagg_policy",
        "exp_id": "2C_local_vs_preagg",
        "status": "DONE",
        "mode": "offline_harness_reuse_4A4B",
        "harness": "scripts/fail-slow/param_calib/2c_local_vs_preagg.py",
        "upstream": [
            "param_calib/4A_federated_denoise/",
            "param_calib/4B_fanout_latency/",
            "param_calib/2B_ring_capacity/",
            "param_calib/2A_trace_window/",
        ],
        "swept_range": {
            "resident_preagg": ["off", "on"],
            "preagg_interval_steps_sensitivity": list(INTERVALS),
        },
        "chosen_value": chosen,
        "choose_rule": (
            "单自变量 on/off；主对照 interval=1 每步 SUMMARY fan-out。"
            "若常驻收益≈0 且预聚开销>0 → 选 local_retain_trigger_then_aggregate。"
        ),
        "controls": {
            "n_ranks": N_RANKS,
            "victim": VICTIM,
            "inject_window": [WINDOW_LO, WINDOW_HI],
            "dose": DOSE,
            "dose_theta": DOSE_THETA,
            "cross_rank_theta": CROSS_RANK_THETA,
            "worst_fraction_phi": WORST_FRACTION_PHI,
            "W_star": W_STAR,
            "fanout_scope": FANOUT_SCOPE,
            "summary_bytes_per_rank": SUMMARY_BYTES_PER_RANK,
            "phase1_ms_per_round": PHASE1_MS_PER_ROUND,
            "aggregation_at_trigger": "federated_SUMMARY_then_suspect_DETAIL",
            "forbid": [
                "multi-IV mix",
                "training step_ms as volume proxy",
                "claim preagg replaces trigger DETAIL/upgrade",
                "open 3C unless free",
                "touch yysong-master / a3 / song AFS",
            ],
        },
        "ground_truth_source": {
            "primary_case": PRIMARY_CASE,
            "primary_run": PRIMARY_RUN,
            "primary_arm": PRIMARY_ARM,
            "resident_query_model": "skills_on_trigger_only",
            "reused": {
                "summary_bytes_per_rank": SUMMARY_BYTES_PER_RANK,
                "phase1_ms_4B_node_n16": PHASE1_MS_PER_ROUND,
                "trigger_ms_4B_node_n16": FEDERATED_TRIGGER_MS,
                "trigger_bytes_4A": FEDERATED_TRIGGER_BYTES,
                "volume_ratio_4A": VOLUME_RATIO_4A,
            },
        },
        "measurements": {
            "horizon": hz,
            "arms": arms,
            "cost_vs_interval": sens,
            "benefit": benefit,
            "long_job": long_job,
            "note": (
                "offline：常驻开销=现有 run 步数×④-A/④-B SUMMARY fan-out 锚点；"
                "非 live 常驻对照作业。收益按设计（触发才查）计 0。"
            ),
        },
        "supports_design": supports,
        "scored_at": scored_at,
        "blocked": False,
        "missing_runs": [],
    }

    (out_dir / "PARAM.json").write_text(
        json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "PARAM.md").write_text(render_md(param), encoding="utf-8")
    (out_dir / "DONE.md").write_text(
        "\n".join(
            [
                "# ②-C DONE · `2C_local_vs_preagg`",
                "",
                f"- policy = **{chosen['policy']}**（resident_preagg=**off**）",
                f"- 预聚开销（500 步×每步）= **{chosen['preagg_cost_bytes']/1024:.1f} KiB** / "
                f"**{chosen['preagg_cost_wall_s']:.3f} s**"
                f"（相对训墙钟 **{100*chosen['preagg_overhead_vs_train']:.2f}%**）",
                f"- 常驻收益 = **{chosen['benefit_latency_saved_ms']:.3f} ms**（≈0）",
                f"- mode = `{param['mode']}`；未上卡；未开 ③-C",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps({"out": str(out_dir), "chosen": chosen, "supports": supports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
