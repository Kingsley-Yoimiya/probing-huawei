#!/usr/bin/env python3
"""Pillar-C E1 正式判分：在线采集 dump 后按步截窗（offline truncate）。

与 E1-off 同尺：
  - P1-SW-C：torch_trace post-forward duration 尖刺（≥3×中位且 ≥0.4s）
  - 锚在 inject_stop=300；禁止训练 step_ms / cold MiB

必须在 SUMMARY 标明 window_mode=offline_truncate（尚无 online retention API）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# 复用 E1-off 解析/判分
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from e1_offline_window_score import (  # noqa: E402
    INJECT_ONSET,
    INJECT_STOP,
    judge_p1_sw_c,
    pick_torch_trace,
    read_memt,
    step_bounds,
    truncate_by_w,
)


def dir_bytes(p: Path) -> int:
    if not p.is_dir():
        return 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def find_set_ok(arm_dir: Path) -> dict[str, Any]:
    hits = list(arm_dir.rglob("set_upgrade.log"))
    if not hits:
        return {"ok": False, "note": "set_upgrade.log_missing"}
    text = hits[0].read_text(encoding="utf-8", errors="replace")
    ok = "SET_OK_WORKER" in text or ("SET_OK" in text and "SET_FAIL_ALL" not in text)
    return {"ok": ok, "path": str(hits[0]), "note": "SET_OK" if ok else "SET_FAIL_or_partial"}


def score_arm(
    arm_dir: Path,
    case: str,
    windows: list[Optional[int]],
) -> dict[str, Any]:
    if not arm_dir.is_dir():
        return {"status": "BLOCKED", "reason": f"missing arm_dir {arm_dir}"}

    try:
        tt_path, pid = pick_torch_trace(arm_dir, case)
    except FileNotFoundError as exc:
        return {"status": "BLOCKED", "reason": str(exc)}

    tt = read_memt(tt_path)
    if not tt.rows:
        return {
            "status": "BLOCKED",
            "reason": "torch_trace MEMT empty",
            "path": str(tt_path),
        }

    mn, mx, steps = step_bounds(tt.rows)
    if INJECT_STOP in steps:
        anchor = INJECT_STOP
    else:
        le = [s for s in steps if s <= INJECT_STOP]
        anchor = max(le) if le else mx

    window_rows = []
    w_star = None
    for w in windows:
        win = truncate_by_w(tt.rows, w, anchor_step=anchor)
        if case == "P1-SW-C":
            j = judge_p1_sw_c(win, w)
        else:
            j = {"enough": False, "evidence": f"unsupported_case:{case}", "primary": "?"}
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

    set_info = find_set_ok(arm_dir)
    probing_data = arm_dir / "probing_data"
    total_b = dir_bytes(probing_data) if probing_data.is_dir() else dir_bytes(arm_dir)

    status = "OK" if w_star is not None else "NO_W_STAR"
    return {
        "status": status,
        "case": case,
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
        "W_star": w_star,
        "windows": window_rows,
        "set_upgrade": set_info,
        "total_bytes_probing_data": total_b,
        "window_mode": "offline_truncate",
        "method": {
            "online": "resident rate≈0 + SET on,rate=1.0 @ inject onset",
            "truncate": (
                f"anchor=inject_stop({INJECT_STOP}); keep local_step in (anchor-W, anchor]; "
                "NOT online retention API"
            ),
            "primary": "torch_trace.duration_spike (P1-SW-C)",
            "forbid": "training step_ms / cold-only MiB",
        },
    }


def render_md(result: dict[str, Any], *, parent: str, arm_dir: str, resident_rate: str) -> str:
    lines = [
        "# E1 · 追溯窗正式验证（P1-SW-C）",
        "",
        "> **定位**：EVAL-GAP §2 E1。在线极稀常驻 + onset SET↑，**dump 后按步截窗**重判。",
        "> **window_mode**：`offline_truncate`（尚无 online「只留最近 W 步」API；与 E1-off 同尺）。",
        "> **尺**：采集归因（duration 尖刺）；**禁止**只用 cold / **禁止**训练 step_ms 假同 D。",
        "",
        "## 配置",
        "",
        f"- parent：`{parent}`",
        f"- arm：`{arm_dir}`（resident_rate={resident_rate}）",
        f"- case：`{result.get('case')}`",
        f"- SET↑：`{result.get('set_upgrade', {}).get('note')}`",
        f"- probing_data 总字节：`{result.get('total_bytes_probing_data')}`",
        "",
        "## 结论",
        "",
    ]
    if result.get("status") == "BLOCKED":
        lines += [f"**BLOCKED**：{result.get('reason')}", ""]
        return "\n".join(lines) + "\n"

    w_star = result.get("W_star")
    lines += [
        f"- **status** = `{result.get('status')}`",
        f"- **W\\*** = `{w_star}`（首次 enough=true；期望对照 E1-off=100）",
        f"- anchor_step=`{result.get('anchor_step')}` inject=[{result.get('inject_onset')},{result.get('inject_stop')}]",
        f"- 环内 steps=`{result.get('n_unique_steps')}` "
        f"({result.get('step_min')}..{result.get('step_max')}) "
        f"rows_ow=`{result.get('ring_rows_overwritten')}`",
        f"- torch_trace：`{result.get('torch_trace_path')}` (pid={result.get('victim_pid')})",
        "",
        "## 分窗（重点 W=50 / 100 / 200）",
        "",
        "| W | enough | n_steps | evidence |",
        "|---:|:---:|---:|---|",
    ]
    for w in result.get("windows") or []:
        lines.append(
            f"| {w['W']} | {'Y' if w['enough'] else 'N'} | {w.get('n_tt_steps')} | `{w.get('evidence')}` |"
        )

    # 对照解读
    by_w = {str(w["W"]): w for w in (result.get("windows") or [])}
    w50 = by_w.get("50")
    w100 = by_w.get("100")
    w200 = by_w.get("200")
    lines += ["", "## 对照解读", ""]
    if w50 and w100 and w200:
        lines.append(
            f"- W=50：{'够' if w50['enough'] else '偏紧/不够'} — `{w50.get('evidence')}`"
        )
        lines.append(
            f"- W=100：{'够' if w100['enough'] else '不够'} — `{w100.get('evidence')}`"
        )
        lines.append(
            f"- W=200：{'够' if w200['enough'] else '不够'}（对照） — `{w200.get('evidence')}`"
        )
    if w_star == "100" or (w100 and w100["enough"] and w50 and not w50["enough"]):
        lines.append(
            "- **与 E1-off 一致**：W*=100 首次够归因；W=50 偏紧、W=200 对照仍够。"
        )
    elif w_star is not None:
        lines.append(f"- 本轮 W*={w_star}（相对 E1-off=100 的差异见 evidence）。")
    else:
        lines.append("- ⚠ NO_W_STAR：全程窗仍不够 duration 尖刺；未用 cold 冒充。")

    lines += [
        "",
        "## 方法备注",
        "",
        f"- online：`{result.get('method', {}).get('online')}`",
        f"- truncate：`{result.get('method', {}).get('truncate')}`",
        f"- forbid：`{result.get('method', {}).get('forbid')}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", type=Path, required=True)
    ap.add_argument("--arm-dir", default="rate_0")
    ap.add_argument("--case", default="P1-SW-C")
    ap.add_argument(
        "--windows",
        nargs="+",
        default=["50", "100", "200", "full"],
        help="W 列表；full=全程≤anchor",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    wins: list[Optional[int]] = []
    for tok in args.windows:
        if tok.lower() in ("full", "none", "all"):
            wins.append(None)
        else:
            wins.append(int(tok))
    # 保证判分顺含 10/25 便于对齐 E1-off（若用户只给了 50/100/200 也 OK）
    arm = args.parent_local / args.arm_dir
    result = score_arm(arm, args.case, wins)

    # resident rate from arm dir name
    resident = args.arm_dir.replace("rate_", "") if args.arm_dir.startswith("rate_") else "?"
    parent = args.parent_local.name
    md = render_md(result, parent=parent, arm_dir=args.arm_dir, resident_rate=resident)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    summary = {
        "parent": parent,
        "arm_dir": args.arm_dir,
        "case": args.case,
        "status": result.get("status"),
        "W_star": result.get("W_star"),
        "window_mode": "offline_truncate",
        "windows": result.get("windows"),
        "set_upgrade": result.get("set_upgrade"),
        "total_bytes_probing_data": result.get("total_bytes_probing_data"),
        "torch_trace_path": result.get("torch_trace_path"),
    }
    (args.out.parent / "E1_WINDOW.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[e1_score] wrote {args.out}")
    print(f"[e1_score] status={result.get('status')} W*={result.get('W_star')}")
    return 0 if result.get("status") != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
