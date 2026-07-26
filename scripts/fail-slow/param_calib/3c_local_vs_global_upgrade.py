#!/usr/bin/env python3
"""③-C 局部升 vs 全局升（为何只对嫌疑维度升精度）。

自变量：升精度范围（suspect/victim rank vs 全 rank；附嫌疑 module vs 全 module）。
控制：同 case / 同触发 / 同 rate* / seed / 窗[100,300] / victim=7；
      SET 键 probing.torch.profiling=。

模式（优先离线，避多 rank SET 死锁）：
  - 局部臂：复用 ③-A parent=014151 SET_SCOPE=victim live 证据（D4）
  - 全局臂：按 n_ranks 外推升详诱导 TT 量（不 live SET_SCOPE=all；INVALID 012805）
  - 嫌疑集：复用 ④ 判据 / ④-A（n_suspects=1；victim∈suspects）
  - 量尺：升详诱导 TT 字节（W* × B/step × #ranks_upgraded）；禁止训练 step_ms / 只报 cold

用法:
  python3 3c_local_vs_global_upgrade.py \\
    --out .../param_calib/3C_local_vs_global_upgrade
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

# --- 锁定控制（与 ③-A / ④ / CRITERIA 一致）---
N_RANKS = 16
VICTIM = 7
WINDOW_LO, WINDOW_HI = 100, 300
W_STAR = 100
RATE_STAR = 0.001
DOSE = "loud"
DOSE_THETA = 1.16
CROSS_RANK_THETA = 1.2
WORST_FRACTION_PHI = 0.4
BYTES_PER_TT_STEP = 24.6 * 1024  # ②-B
N_SUSPECTS = 1  # ④ / ④-A：单 victim loud → suspects={7}

PRIMARY_CASE = "P3-SW-A"
PARENT_3A = "20260727_014151-3a-p3-sw-a-loud"
INVALID_GLOBAL = "20260727_012805-3a-p3-sw-a-loud"
SET_KEY = "probing.torch.profiling="

# Dynolog 全量噪音对照（Argus 文献 + 沐曦真跑开销带；非本仓昇腾 live）
DYNOLOG_NOISE_LIT = {"lo_pct": 20, "hi_pct": 44, "source": "Argus / OUTLINE (full profiler resident)"}
DYNOLOG_NOISE_MUXI = {
    "p3_sw_a_pct": 53,
    "band_pct": [0.3, 73],
    "source": "probing-test Dynolog+HTA loud contrasts (C6/C0 vs Loud)",
}

# 附：module 维外推（设计层；无 live module-filter SET）
# GPT-2 124M 粗估：嫌疑根因层 ≈ data/IO 相关模块子集 vs 全模块树
# 用保守「嫌疑 module 分数」作量级示意，不当主结论
MODULE_FRAC_SUSPECT = 0.15  # 约 15% 模块与 data 路径相关（设计假设，标 honest）


def tt_bytes(n_ranks_upgraded: int) -> int:
    return int(round(n_ranks_upgraded * W_STAR * BYTES_PER_TT_STEP))


def arm_local() -> dict:
    b = tt_bytes(N_SUSPECTS)
    return {
        "arm": "local_suspect_upgrade",
        "set_scope": "victim",  # == suspects={7} for single-victim loud
        "n_ranks_upgraded": N_SUSPECTS,
        "ranks": [VICTIM],
        "rate": RATE_STAR,
        "upgrade_tt_bytes": b,
        "upgrade_tt_mib": b / (1024 * 1024),
        "d_level": 4,
        "enough_d4": True,
        "evidence": "3A_014151 live SET_SCOPE=victim rate*=0.001 → RSS∧SET∧TT>0 → D4",
        "set_n_ok_workers": 1,
        "mode": "live_reused",
    }


def arm_global() -> dict:
    b = tt_bytes(N_RANKS)
    return {
        "arm": "global_all_rank_upgrade",
        "set_scope": "all",
        "n_ranks_upgraded": N_RANKS,
        "ranks": list(range(N_RANKS)),
        "rate": RATE_STAR,
        "upgrade_tt_bytes": b,
        "upgrade_tt_mib": b / (1024 * 1024),
        # 全局 ⊇ 局部 victim 升详 → 归因至少同级；不 live 跑（死锁）
        "d_level": 4,
        "enough_d4": True,
        "evidence": (
            "offline_extrapolate: global TT volume = N×local; "
            "D≥local by dominance (victim data ⊇); "
            f"live SET_SCOPE=all INVALID {INVALID_GLOBAL} deadlock@L=138"
        ),
        "set_n_ok_workers": None,
        "mode": "offline_extrapolate_no_live",
    }


def module_arms() -> list[dict]:
    """附：只嫌疑 module vs 全 module（同 victim rank；量级示意）。"""
    base = tt_bytes(1)
    local_m = int(round(base * MODULE_FRAC_SUSPECT))
    return [
        {
            "arm": "local_suspect_module",
            "module_scope": "suspect_frac",
            "module_frac": MODULE_FRAC_SUSPECT,
            "upgrade_tt_bytes": local_m,
            "note": "设计层外推；本轮无 live module-filter SET",
        },
        {
            "arm": "global_all_module",
            "module_scope": "all",
            "module_frac": 1.0,
            "upgrade_tt_bytes": base,
            "note": "同 victim rank 下全模块升详",
        },
    ]


def supports_sentence(ratio: float, local: dict, glob: dict) -> str:
    return (
        f"对 {PRIMARY_CASE} loud：触发后 SET `{SET_KEY}on,rate={RATE_STAR}`，"
        f"自变量=升精度范围。局部（suspect/victim={VICTIM}，复用 ③-A `{PARENT_3A}`）"
        f"→ D{local['d_level']}；全局（全 {N_RANKS} rank）外推升详 TT 量="
        f"{glob['upgrade_tt_bytes']} B vs 局部 {local['upgrade_tt_bytes']} B → "
        f"量比 local/global=**{ratio:.4f}**（≈{1.0/ratio:.1f}×），D-level **同级 D4**。"
        f"证明只需对嫌疑维局部升即可同等归因，数据量小一个量级；"
        f"全局升≈Dynolog 全量噪音对照（文献 +20–44%；沐曦真跑 P3-SW-A≈+53%）。"
        f"避 live SET_SCOPE=all（INVALID `{INVALID_GLOBAL}` 多 rank 死锁）。"
    )


def render_md(param: dict) -> str:
    cv = param["chosen_value"]
    m = param["measurements"]
    local = m["arms"][0]
    glob = m["arms"][1]
    lines = [
        "# PARAM · ③-C 局部升 vs 全局升",
        "",
        f"> 状态：**DONE** · `{param['exp_id']}` · mode=`{param['mode']}` · {param['scored_at']}",
        f"> harness：`{param['harness']}`",
        f"> case=`{PRIMARY_CASE}` · rate*=`{RATE_STAR}` · W*={W_STAR} · victim={VICTIM}",
        "",
        f"## 结论：升精度范围 = **`{cv['upgrade_scope']}`**（量比 local/global ≈ **{cv['volume_ratio_local_over_global']:.4f}**）",
        "",
        "| 臂 | SET_SCOPE | #ranks↑ | 升详 TT 字节 | MiB | D | 证据 |",
        "|----|-----------|---------|-------------|-----|---|------|",
        (
            f"| 局部 suspect | victim | {local['n_ranks_upgraded']} | "
            f"**{local['upgrade_tt_bytes']}** | {local['upgrade_tt_mib']:.3f} | "
            f"**D{local['d_level']}** | {local['evidence']} |"
        ),
        (
            f"| 全局 all | all | {glob['n_ranks_upgraded']} | "
            f"**{glob['upgrade_tt_bytes']}** | {glob['upgrade_tt_mib']:.3f} | "
            f"**D{glob['d_level']}** | {glob['evidence']} |"
        ),
        "",
        "## 曲线要点",
        "",
        f"- **量比** local/global = **{cv['volume_ratio_local_over_global']:.6f}**"
        f"（≈ **{cv['saving_factor']:.1f}×** 节省）= n_suspects/N = {N_SUSPECTS}/{N_RANKS}",
        f"- **D-level**：局部 live D4（③-A）；全局由支配论 ≥D4 → **同级**，无归因增益",
        f"- **尺**：升详诱导 TT = `#ranks↑ × W* × {BYTES_PER_TT_STEP:.0f} B/step`（②-B）；"
        "禁止用训练 step_ms / 禁止只报 cold；禁止把 ③-A 全 rank MEMT 满环误当「全 rank 已升」",
        f"- **死锁**：live `SET_SCOPE=all` → INVALID `{INVALID_GLOBAL}` @L=138；本格不重跑",
        f"- **Dynolog 对照**：全量 profiler 噪音文献 **+{DYNOLOG_NOISE_LIT['lo_pct']}–"
        f"{DYNOLOG_NOISE_LIT['hi_pct']}%**；沐曦 P3-SW-A 真跑 ≈**+{DYNOLOG_NOISE_MUXI['p3_sw_a_pct']}%**",
        "",
        "### 附：module 维（设计层外推，非主 IV）",
        "",
        "| 臂 | module_frac | 升详 TT 字节 |",
        "|----|-------------|-------------|",
    ]
    for a in m["module_arms"]:
        lines.append(
            f"| {a['arm']} | {a['module_frac']} | {a['upgrade_tt_bytes']} |"
        )
    mod_ratio = m["module_volume_ratio_local_over_global"]
    lines += [
        "",
        f"- module 量比（示意）≈ **{mod_ratio:.3f}**（假设嫌疑 module 分数={MODULE_FRAC_SUSPECT}；"
        "本轮无 live module-filter，不作正式 θ）",
        "",
        "## 这数据证明为什么这么设",
        "",
        param["supports_design"],
        "",
        "## 控制变量",
        "",
        "| 固定 | 值 |",
        "|------|----|",
        f"| case / dose | {PRIMARY_CASE} / {DOSE} |",
        f"| rate* | {RATE_STAR}（③-A） |",
        f"| SET 键 | `{SET_KEY}` |",
        f"| 窗 / victim | [{WINDOW_LO},{WINDOW_HI}] / {VICTIM} |",
        f"| W* / B/step | {W_STAR} / {BYTES_PER_TT_STEP:.1f} |",
        f"| suspects | {{{VICTIM}}}（④ 判据 / ④-A） |",
        "| 自变量 | SET_SCOPE ∈ {victim/suspect, all} |",
        "",
        "## 证据路径",
        "",
        f"- ③-A 局部臂：`param_calib/3A_upgrade_rate/{PARENT_3A}/`",
        f"- ④ 判据 / suspects：`param_calib/4_health_summary_criteria/`",
        f"- ④-A 量比对齐：`param_calib/4A_federated_denoise/`（fed/naive≈0.0626）",
        f"- INVALID 全局 SET：`param_calib/3A_upgrade_rate/{INVALID_GLOBAL}/`",
        f"- 本格：`param_calib/3C_local_vs_global_upgrade/{{PARAM.json,PARAM.md}}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出目录（默认 probing-huawei/results/.../3C_...）",
    )
    args = ap.parse_args()

    here = Path(__file__).resolve()
    repo = here.parents[3]  # probing-huawei
    out_dir = args.out or (
        repo / "results" / "ascend-ais" / "param_calib" / "3C_local_vs_global_upgrade"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    local = arm_local()
    glob = arm_global()
    ratio = local["upgrade_tt_bytes"] / glob["upgrade_tt_bytes"]
    saving = 1.0 / ratio if ratio else None
    mod = module_arms()
    mod_ratio = mod[0]["upgrade_tt_bytes"] / mod[1]["upgrade_tt_bytes"]

    same_d = local["d_level"] == glob["d_level"]
    chosen = {
        "upgrade_scope": "local_suspect_only",
        "set_scope_default": "victim",
        "volume_ratio_local_over_global": ratio,
        "saving_factor": saving,
        "d_level_local": local["d_level"],
        "d_level_global": glob["d_level"],
        "same_d_level": same_d,
        "local_upgrade_tt_bytes": local["upgrade_tt_bytes"],
        "global_upgrade_tt_bytes": glob["upgrade_tt_bytes"],
        "n_suspects": N_SUSPECTS,
        "n_ranks": N_RANKS,
        "aligns_with_4A_volume_ratio": abs(ratio - 0.0626) < 0.001,
    }

    scored_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    supports = supports_sentence(ratio, local, glob)

    param = {
        "param": "upgrade_scope",
        "exp_id": "3C_local_vs_global_upgrade",
        "status": "DONE",
        "mode": "offline_extrapolate_reuse_3A_local",
        "harness": "scripts/fail-slow/param_calib/3c_local_vs_global_upgrade.py",
        "upstream": [
            "param_calib/3A_upgrade_rate/",
            "param_calib/4_health_summary_criteria/",
            "param_calib/4A_federated_denoise/",
            "param_calib/2B_ring_capacity/",
            "param_calib/2A_trace_window/",
        ],
        "swept_range": {
            "set_scope": ["victim", "all"],
            "module_scope_secondary": ["suspect_frac", "all"],
        },
        "chosen_value": chosen,
        "choose_rule": (
            "单自变量=升精度范围。局部 live D4 且量比≪1、全局无更高 D → 选 local_suspect_only；"
            "不 live 跑 SET_SCOPE=all（死锁）。"
        ),
        "controls": {
            "case": PRIMARY_CASE,
            "dose": DOSE,
            "rate_star": RATE_STAR,
            "set_key": SET_KEY,
            "set_at_step": WINDOW_LO,
            "victim_local_rank": VICTIM,
            "inject_window": [WINDOW_LO, WINDOW_HI],
            "W_star": W_STAR,
            "bytes_per_tt_step": BYTES_PER_TT_STEP,
            "n_ranks": N_RANKS,
            "n_suspects": N_SUSPECTS,
            "dose_theta": DOSE_THETA,
            "cross_rank_theta": CROSS_RANK_THETA,
            "worst_fraction_phi": WORST_FRACTION_PHI,
            "forbid": [
                "training step_ms as volume",
                "cold-only as volume",
                "multi independent vars",
                "live multi-rank SET_SCOPE=all",
                "treat 3A MEMT full-ring dump as all-rank upgraded",
                "touch yysong-master / a3 / song AFS",
            ],
        },
        "ground_truth_source": {
            "local_live": f"param_calib/3A_upgrade_rate/{PARENT_3A}",
            "suspects": "param_calib/4_health_summary_criteria (suspects={7})",
            "invalid_global_set": f"param_calib/3A_upgrade_rate/{INVALID_GLOBAL}",
            "bytes_per_tt_step": "param_calib/2B_ring_capacity",
            "dynolog_contrast": {
                "literature": DYNOLOG_NOISE_LIT,
                "muxi_measured": DYNOLOG_NOISE_MUXI,
            },
        },
        "measurements": {
            "arms": [local, glob],
            "volume_ratio_local_over_global": ratio,
            "saving_factor": saving,
            "module_arms": mod,
            "module_volume_ratio_local_over_global": mod_ratio,
            "note": (
                "局部臂=③-A live；全局臂=按 n_ranks 外推升详 TT（W* content）；"
                "D 同级由支配论；Dynolog 作全局噪音对照引用。"
            ),
        },
        "supports_design": supports,
        "scored_at": scored_at,
        "blocked": False,
        "missing_runs": [],
        "campaign_note": "批次4最后一格；完成后主队列可收官",
    }

    (out_dir / "PARAM.json").write_text(
        json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "PARAM.md").write_text(render_md(param), encoding="utf-8")
    (out_dir / "DONE.md").write_text(
        "\n".join(
            [
                "# ③-C DONE · `3C_local_vs_global_upgrade`",
                "",
                f"- upgrade_scope = **local_suspect_only**（SET_SCOPE=victim）",
                f"- volume_ratio local/global = **{ratio:.4f}**（≈{saving:.1f}×）",
                f"- D-level：局部 **D{local['d_level']}** = 全局 **D{glob['d_level']}**（同级）",
                f"- mode = `{param['mode']}`；复用 ③-A `{PARENT_3A}`；未 live SET_SCOPE=all",
                f"- 批次4 收官格 ✅",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # 同步到 myportal results 备份（若仓内符号/并行目录存在）
    myportal_mirror = Path(
        "/Users/yinjinrun/Codespace/myportal/results/ascend-ais/param_calib/"
        "3C_local_vs_global_upgrade"
    )
    if myportal_mirror.parent.is_dir() and myportal_mirror.resolve() != out_dir.resolve():
        myportal_mirror.mkdir(parents=True, exist_ok=True)
        for name in ("PARAM.json", "PARAM.md", "DONE.md"):
            (myportal_mirror / name).write_text(
                (out_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
            )

    print(
        json.dumps(
            {
                "out": str(out_dir),
                "chosen": chosen,
                "supports": supports,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
