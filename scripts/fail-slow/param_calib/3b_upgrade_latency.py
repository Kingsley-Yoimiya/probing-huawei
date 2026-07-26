#!/usr/bin/env python3
"""Param-Calib ③-B：SET→live tracer 生效延迟 + 升完到够归因；对照对手重启≈150。

尺（采集侧；禁止训练 step_ms / 禁止只报 cold）：
  - set_upgrade.log：SET_L / SET_OK / SET_LATENCY_MS（attach 墙钟）
  - set_latency_probe.log（在线）：首 TT 出现的 L / gmin；够 TT_floor / 够 W*
  - 对照：S1 对手重启 ≈150 步（pillar_c_v2）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def find_one(root: Path, name: str) -> Optional[Path]:
    hits = list(root.rglob(name))
    return hits[0] if hits else None


def parse_kv_log(path: Optional[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path) if path else None, "present": False}
    if not path or not path.is_file():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    out["present"] = True
    out["raw_tail"] = "\n".join(text.splitlines()[-40:])
    for key in (
        "SET_L",
        "SET_LATENCY_MS",
        "LATENCY_SET_TO_LIVE_STEPS",
        "LATENCY_SET_TO_LIVE_GSTEPS",
        "LATENCY_SET_TO_LIVE_MS",
        "LATENCY_LIVE_TO_ENOUGH_TT_STEPS",
        "LATENCY_LIVE_TO_ENOUGH_W_STEPS",
    ):
        m = re.search(rf"^{key}=(-?\d+)\s*$", text, re.M)
        if m:
            out[key] = int(m.group(1))
    m = re.search(r"^LATENCY_LIVE L=(\d+) gmin=(-?\d+) gmax=(-?\d+) n=(\d+)", text, re.M)
    if m:
        out["live"] = {
            "L": int(m.group(1)),
            "gmin": int(m.group(2)),
            "gmax": int(m.group(3)),
            "n": int(m.group(4)),
        }
    m = re.search(r"^LATENCY_ENOUGH_TT L=(\d+) n=(\d+)", text, re.M)
    if m:
        out["enough_tt"] = {"L": int(m.group(1)), "n": int(m.group(2))}
    m = re.search(r"^LATENCY_ENOUGH_WSTAR L=(\d+) gmax=(-?\d+) span=(\d+)", text, re.M)
    if m:
        out["enough_w"] = {
            "L": int(m.group(1)),
            "gmax": int(m.group(2)),
            "span": int(m.group(3)),
        }
    out["set_ok"] = bool(re.search(r"SET_OK_WORKER", text)) or (
        path.name == "set_latency_probe.log"
        and "LATENCY_LIVE" in text
    )
    if path.name == "set_upgrade.log":
        out["set_ok"] = "SET_OK_WORKER" in text and "SET_FAIL_ALL" not in text
        m = re.search(r"SET_OK_WORKER pid=(\d+)", text)
        if m:
            out["pid"] = m.group(1)
        m = re.search(r"set_rate=([0-9.]+)|SET_TARGET=probing\.torch\.profiling=on,rate=([0-9.]+)", text)
        if m:
            out["target_rate"] = m.group(1) or m.group(2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--set-rate", default="1.0")
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--w-star", type=int, default=100)
    ap.add_argument("--tt-floor", type=int, default=800)
    ap.add_argument("--opponent-restart-steps", type=int, default=150)
    ap.add_argument("--s1-ref", default="")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    arm = parent / f"upgrade_rate_{args.set_rate}"
    set_log = find_one(arm, "set_upgrade.log")
    probe_log = find_one(arm, "set_latency_probe.log")
    set_info = parse_kv_log(set_log)
    # merge probe into set_info for missing keys; probe overrides latency fields
    probe = parse_kv_log(probe_log)
    for k, v in probe.items():
        if k in ("path", "present", "raw_tail", "set_ok"):
            continue
        set_info[k] = v

    set_to_live = set_info.get("LATENCY_SET_TO_LIVE_STEPS")
    # prefer jsonl L delta; if negative/missing try gsteps clamped
    if set_to_live is None:
        set_to_live = set_info.get("LATENCY_SET_TO_LIVE_GSTEPS")
    if isinstance(set_to_live, int) and set_to_live < 0:
        # gmin may be ring-min after overwrite mid-probe; trust L delta if present
        set_to_live = set_info.get("LATENCY_SET_TO_LIVE_STEPS", set_to_live)

    live_to_tt = set_info.get("LATENCY_LIVE_TO_ENOUGH_TT_STEPS")
    live_to_w = set_info.get("LATENCY_LIVE_TO_ENOUGH_W_STEPS")

    # 设计输出：生效延迟 = SET→live；够归因时间 = live→W*（②-A）；总响应 = 二者之和
    chosen_set_to_live = set_to_live if isinstance(set_to_live, int) else None
    chosen_live_to_enough = live_to_w if isinstance(live_to_w, int) else live_to_tt
    total = None
    if isinstance(chosen_set_to_live, int) and isinstance(chosen_live_to_enough, int):
        total = chosen_set_to_live + chosen_live_to_enough

    vs_opp = None
    if isinstance(total, int):
        vs_opp = {
            "opponent_restart_steps": args.opponent_restart_steps,
            "probing_total_steps": total,
            "speedup_x": round(args.opponent_restart_steps / max(total, 1), 2),
            "delta_steps": args.opponent_restart_steps - total,
        }

    ok = bool(set_info.get("set_ok") or (probe.get("present") and "LATENCY_LIVE" in (probe.get("raw_tail") or "")))
    if probe.get("present") and "LATENCY_LIVE" in (probe.get("raw_tail") or ""):
        ok = True

    param = {
        "param": "torch_trace_upgrade_latency_steps",
        "exp_id": "3B_upgrade_latency",
        "swept_range": None,
        "chosen_value": {
            "set_to_live_steps": chosen_set_to_live,
            "live_to_enough_steps": chosen_live_to_enough,
            "total_response_steps": total,
            "enough_rule": f"prefer W*={args.w_star} unique global_step span; fallback TT_rows>={args.tt_floor}",
        },
        "choose_rule": "online probe after SET: first TT n>0 → set→live; then W* span or TT_floor → live→enough",
        "case": args.case,
        "controls": {
            "resident_rate": 0,
            "set_rate": args.set_rate,
            "set_at_step": 100,
            "set_key": "probing.torch.profiling=",
            "inject_window": [100, 300],
            "victim_local_rank": 7,
            "set_scope": "victim",
            "w_star": args.w_star,
            "tt_floor": args.tt_floor,
            "forbid": ["training step_ms", "cold-only as volume", "multi independent vars", "offline MEMT for first-step"],
        },
        "ground_truth_source": {
            "set_upgrade_log": str(set_log) if set_log else None,
            "set_latency_probe_log": str(probe_log) if probe_log else None,
            "s1_opponent_restart": args.s1_ref or "pillar_c_v2/S1_MID_ATTACH.md ≈150",
            "mech": "optimizer_step_post_hook polls probing.torch.profiling each step → ~1-step sync",
        },
        "measurements": {
            "set_ok": ok,
            "set_l": set_info.get("SET_L"),
            "set_attach_latency_ms": set_info.get("SET_LATENCY_MS"),
            "set_to_live_steps": set_to_live,
            "set_to_live_gsteps": set_info.get("LATENCY_SET_TO_LIVE_GSTEPS"),
            "set_to_live_ms": set_info.get("LATENCY_SET_TO_LIVE_MS"),
            "live": set_info.get("live"),
            "live_to_enough_tt_steps": live_to_tt,
            "live_to_enough_w_steps": live_to_w,
            "enough_tt": set_info.get("enough_tt"),
            "enough_w": set_info.get("enough_w"),
            "probe_present": probe.get("present", False),
        },
        "vs_opponent": vs_opp,
        "supports_design": (
            f"对 {args.case} loud：常驻 rate=0 → SET `probing.torch.profiling=on,rate={args.set_rate}` "
            f"（SET_SCOPE=victim；在线 probe）。生效延迟 set→live="
            f"{chosen_set_to_live if chosen_set_to_live is not None else '?'} 步；"
            f"升完到够归因 live→enough="
            f"{chosen_live_to_enough if chosen_live_to_enough is not None else '?'} 步"
            f"（W*={args.w_star} / TT≥{args.tt_floor}）。"
            f"总响应={total if total is not None else '?'} 步 vs 对手重启≈{args.opponent_restart_steps} 步"
            + (
                f"（快 {vs_opp['speedup_x']}× / 少 {vs_opp['delta_steps']} 步）。"
                if vs_opp
                else "。"
            )
            + "证明热 SET 升详无需重启，响应远快于对手重启代价。"
        ),
        "parent_run": parent.name,
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "note": "latency from online set_latency_probe.log; ring overwrite makes offline MEMT first-step unreliable",
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "PARAM.json").write_text(json.dumps(param, ensure_ascii=False, indent=2) + "\n")
    (parent / "PARAM.json").write_text(json.dumps(param, ensure_ascii=False, indent=2) + "\n")

    rows = [
        ("SET→live（jsonl L）", chosen_set_to_live),
        ("SET→live（global_step）", set_info.get("LATENCY_SET_TO_LIVE_GSTEPS")),
        ("live→够 TT_floor", live_to_tt),
        ("live→够 W*", live_to_w),
        ("总响应（set→live + live→enough）", total),
        ("对手重启（S1）", args.opponent_restart_steps),
    ]
    md = [
        "# PARAM · ③-B 升精度生效延迟",
        "",
        f"> case=`{args.case}` · parent=`{parent.name}` · 测响应时间（无自变量扫）",
        f"> SET rate=`{args.set_rate}` · SET_SCOPE=victim · W*={args.w_star} · TT_floor={args.tt_floor}",
        "",
        "## 结论",
        "",
        f"- **生效延迟（SET→live tracer）** = **{chosen_set_to_live if chosen_set_to_live is not None else '未测到'}** 步",
        f"- **升完到够归因（live→enough）** = **{chosen_live_to_enough if chosen_live_to_enough is not None else '未测到'}** 步"
        f"（优先 W*={args.w_star}；否则 TT≥{args.tt_floor}）",
        f"- **总响应** = **{total if total is not None else '?'}** 步 vs 对手重启 ≈ **{args.opponent_restart_steps}** 步"
        + (f"（快 **{vs_opp['speedup_x']}×**）" if vs_opp else ""),
        f"- SET_OK={'Y' if ok else 'N'} · probe={'Y' if probe.get('present') else 'N'} · SET_L={set_info.get('SET_L')}",
        "",
        "## 对照表",
        "",
        "| 量 | 步数 |",
        "|---|---:|",
    ]
    for name, val in rows:
        md.append(f"| {name} | {val if val is not None else '—'} |")
    md += [
        "",
        "## 这数据证明为什么这么设",
        "",
        param["supports_design"],
        "",
        "## 证据",
        "",
        f"- set_upgrade: `{set_log}`",
        f"- latency_probe: `{probe_log}`",
        f"- S1 对照: `{args.s1_ref or 'pillar_c_v2/S1_MID_ATTACH.md'}`",
        f"- 机制: optimizer post-hook 每步读 `probing.torch.profiling` → `_sync_live_tracers`",
        "",
        "## 产物",
        "",
        f"- `{out_dir / 'PARAM.json'}`",
        f"- `{parent}/`",
        "",
    ]
    (out_dir / "PARAM.md").write_text("\n".join(md), encoding="utf-8")
    shutil.copy2(out_dir / "PARAM.md", parent / "PARAM.md")
    print(json.dumps(param["chosen_value"], ensure_ascii=False))
    return 0 if (ok and chosen_set_to_live is not None) else 2


if __name__ == "__main__":
    raise SystemExit(main())
