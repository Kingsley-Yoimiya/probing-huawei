#!/usr/bin/env python3
"""PR-2 实验 B：E3 数据量比 + SQL localize 语义验收（dense rank == culprit）。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# 复用 v2 E3 尺
sys.path.insert(0, str(Path(__file__).resolve().parent))
import e3_score_ratio as e3  # noqa: E402

INJECT_STOP = 300
DENSE_ROW_FLOOR = 1


def parse_localize_meta(arm: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("set_upgrade.log", "localize.log"):
        p = e3.find_one(arm, name)
        if not p or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for key, pat in (
            ("culprit_rank", r"CULPRIT_RANK=(\d+)"),
            ("culprit_pid", r"CULPRIT_PID=(\d+)"),
            ("localize_fallback", r"LOCALIZE_FALLBACK=(\d+)"),
            ("localize_elapsed_ms", r"LOCALIZE_ELAPSED_MS=(\d+)"),
        ):
            m = re.search(pat, text)
            if m and key not in out:
                out[key] = int(m.group(1))
        m = re.search(r"culprit_rank=(\d+)", text)
        if m and "culprit_rank" not in out:
            out["culprit_rank"] = int(m.group(1))
        m = re.search(r"culprit_pid=(\d+)", text)
        if m and "culprit_pid" not in out:
            out["culprit_pid"] = int(m.group(1))
        m = re.search(r"fallback=(True|False)", text)
        if m and "localize_fallback_bool" not in out:
            out["localize_fallback_bool"] = m.group(1) == "True"
        if name == "localize.log":
            first = text.splitlines()[0] if text else ""
            if first:
                out["localize_head"] = first[:500]
    if "localize_fallback" in out:
        out["localize_sql"] = out["localize_fallback"] == 0
    return out


def torch_trace_per_rank(pdata: Path, w_star: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not pdata.is_dir():
        return rows
    for f in sorted(pdata.rglob("python.torch_trace")):
        if not f.is_file():
            continue
        pid = f.parent.name if f.parent != pdata else "?"
        info = e3.memt_step_info(f)
        n_rows = int(info.get("n_rows") or 0)
        steps = info.get("steps") or []
        w_est = e3.estimate_w_truncate_tt_bytes(
            {
                "file_bytes": int(info.get("file_bytes") or f.stat().st_size),
                "steps": steps,
                "n_steps": info.get("n_steps"),
            },
            w_star,
            anchor=INJECT_STOP,
        )
        if n_rows == 0:
            w_est = {
                **w_est,
                "est_tt_bytes_w": 0,
                "n_steps_w": 0,
                "note": "empty_ring_0_rows",
            }
        rows.append(
            {
                "pid": pid,
                "path": str(f),
                "file_bytes": int(info.get("file_bytes") or 0),
                "n_rows": n_rows,
                "n_steps": int(info.get("n_steps") or 0),
                "step_min": info.get("step_min"),
                "step_max": info.get("step_max"),
                "n_steps_w": w_est.get("n_steps_w"),
                "est_tt_bytes_w": w_est.get("est_tt_bytes_w"),
                "note": w_est.get("note"),
                "dense": n_rows >= DENSE_ROW_FLOOR,
            }
        )
    rows.sort(key=lambda x: (-int(x.get("n_rows") or 0), x.get("pid", "")))
    return rows


def enrich_dynamic(dyn_row: dict[str, Any], arm: Path, w_star: int) -> dict[str, Any]:
    pdata = Path(dyn_row.get("probing_data") or arm / "probing_data")
    if not pdata.is_dir():
        hits = [p for p in arm.rglob("probing_data") if p.is_dir()]
        pdata = hits[0] if hits else pdata
    per_rank = torch_trace_per_rank(pdata, w_star)
    dense = [r for r in per_rank if r.get("dense")]
    loc = parse_localize_meta(arm)

    # W* content est：空 rank TT 按 0；dense rank 按步比例
    raw_tt = sum(int(r.get("file_bytes") or 0) for r in per_rank)
    est_tt_w = sum(int(r.get("est_tt_bytes_w") or 0) for r in per_rank)
    raw_total = int(dyn_row.get("total_dump_bytes") or 0)
    dyn_adj = raw_total - raw_tt + est_tt_w if raw_tt > 0 else None

    culprit_pid = str(loc.get("culprit_pid", ""))
    dense_pids = [r["pid"] for r in dense]
    dense_rank_match = culprit_pid in dense_pids if culprit_pid else None

    dyn_row["localize"] = loc
    dyn_row["torch_trace_ranks"] = [{k: v for k, v in r.items() if k != "dense"} for r in per_rank]
    dyn_row["torch_trace_dense_ranks"] = len(dense)
    dyn_row["torch_trace_dense_pids"] = dense_pids
    dyn_row["torch_trace_bytes_w_star_est"] = est_tt_w
    if dyn_adj is not None:
        dyn_row["total_dump_bytes_w_star_est"] = dyn_adj
    dyn_row["w_truncate"] = {
        "mode": "offline_truncate_estimate",
        "w": w_star,
        "anchor": INJECT_STOP,
        "note": "empty_rank_tt_counted_0; dense_rank_scaled_by_steps_in_W; PR2_SQL_localize_not_break_pid",
    }
    dyn_row["dense_rank_matches_culprit"] = dense_rank_match
    dyn_row["semantic"] = "orchestration_sql_localize_culprit_only_set"
    return dyn_row


def write_pr2_md(summary: dict[str, Any], out_md: Path, *, v2_ref_pct: float = 72.6) -> None:
    dyn = summary.get("dynamic") or {}
    loc = dyn.get("localize") or {}
    headline = summary.get("headline_pct")
    dense_n = dyn.get("torch_trace_dense_ranks")
    culprit = loc.get("culprit_rank")
    match = dyn.get("dense_rank_matches_culprit")

    lines = [
        "# PR2_E3_RATIO · 编排层 SQL 定位 + 仅 culprit 升详",
        "",
        f"> case=`{summary.get('case')}` · parent=`{Path(summary.get('parent', '')).name}`",
        f"> 动态臂复用：`{summary.get('dynamic_reuse_run', '—')}` · 全量臂：`{'reuse v2' if (summary.get('full') or {}).get('reuse') else 'fresh'}`",
        "> **语义**：编排层 SQL 定位 culprit（判据查询期现场写），**仅对 culprit SET rate=1.0**；非 break 抢首个 worker pid。",
        "",
        f"## 结论：动态/全量 = **{headline}%**（{summary.get('headline_note')}）",
        "",
        f"- v2 参考头条：**{v2_ref_pct}%**（`pillar_c_v2/20260726_181423-…`）",
        f"- raw 总落盘比：`{summary.get('ratio_raw_pct')}%`",
        f"- W\\* content est：`{summary.get('ratio_w_star_pct')}%`",
        f"- 同覆盖（RSS 够归因）：**{'Y' if summary.get('same_cover') else 'N'}**",
        "",
        "## PR-2 验收",
        "",
        f"| 项 | 值 | 判据 |",
        f"|----|-----|------|",
        f"| `torch_trace_dense_ranks` | **{dense_n}** | == 1 |",
        f"| `culprit_rank` (SQL) | **{culprit}** | GT=7 |",
        f"| dense pid == culprit pid | **{'Y' if match else 'N'}** | Y |",
        f"| `LOCALIZE_FALLBACK` | **{loc.get('localize_fallback', '?')}** | 0 |",
        f"| SET | **{(dyn.get('set') or {}).get('note', '?')}** | SET_OK |",
        "",
        "### 语义翻转",
        "",
        "- v2：脚本在首个 ATTACH_OK worker 后 `break` → dense rank 碰运气。",
        "- v3 PR-2：`pillar_c_localize_culprit.py` SQL 定 culprit → `PILLAR_C_SET_SCOPE=localize` 仅 1 pid 升详。",
        "",
        f"- localize 首行：`{(loc.get('localize_head') or '—')[:200]}`",
        "",
        "## 分臂字节表",
        "",
        "| 臂 | total_B | MiB | cold_B | RSS | SET | 备注 |",
        "|----|--------:|----:|-------:|:---:|:---:|------|",
    ]
    for label, row in (("动态", dyn), ("全量", summary.get("full") or {})):
        tb = row.get("total_dump_bytes") or 0
        lines.append(
            f"| {label} | {tb} | {tb/1024/1024:.2f} | {row.get('cold_bytes') or '—'} | "
            f"{'Y' if row.get('enough') else 'N'} | "
            f"{(row.get('set') or {}).get('note', 'n/a') if label == '动态' else 'n/a'} | "
            f"{row.get('semantic') or row.get('evidence_note') or ''} |"
        )
    w_adj = dyn.get("total_dump_bytes_w_star_est")
    if w_adj:
        lines.append(
            f"| 动态·W\\*估 | {w_adj} | {w_adj/1024/1024:.2f} | — | "
            f"{'Y' if dyn.get('enough') else 'N'} | {(dyn.get('set') or {}).get('note', '?')} | W*={summary.get('w_star')} |"
        )

    dense_ranks = dyn.get("torch_trace_ranks") or []
    if dense_ranks:
        lines += ["", "### torch_trace 分 rank（dense=行数>0）", "", "| pid | rows | steps | file_B | W* est_B |", "|-----|-----:|------:|-------:|---------:|"]
        for r in dense_ranks[:16]:
            mark = "**" if str(r.get("pid")) == str(loc.get("culprit_pid")) else ""
            lines.append(
                f"| {mark}{r.get('pid')}{mark} | {r.get('n_rows')} | {r.get('n_steps')} | "
                f"{r.get('file_bytes')} | {r.get('est_tt_bytes_w')} |"
            )

    verdict = "PASS" if (
        dense_n == 1
        and culprit == 7
        and match
        and loc.get("localize_fallback") == 0
        and (dyn.get("set") or {}).get("ok")
        and headline is not None
    ) else "PARTIAL"

    lines += [
        "",
        f"## 判定：**{verdict}**",
        "",
        f"- JSON：`PR2_E3_RATIO.json`",
        f"- 本机：`{summary.get('parent')}`",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--dynamic-arm", required=True, help="动态臂本地目录（upgrade_rate_* 或 rate_*）")
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--w-star", type=int, default=100)
    ap.add_argument("--resident-rate", default="0")
    ap.add_argument("--full-ref", default="")
    ap.add_argument("--dynamic-reuse-run", default="")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--v2-ref-pct", type=float, default=72.6)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    dyn_arm = Path(args.dynamic_arm)
    full_local = parent / "full_fidelity"
    full_bytes_file = full_local / "total_dump_bytes.txt"

    dyn_row = e3.score_arm(dyn_arm, "dynamic")
    dyn_row = enrich_dynamic(dyn_row, dyn_arm, args.w_star)

    full_row: dict[str, Any] = {"label": "full_fidelity", "reuse": (full_local / "REUSE.txt").is_file()}
    if (full_local / "probing_data").is_dir():
        full_row = e3.score_arm(full_local, "full_fidelity")
        full_row["reuse"] = False
    elif full_bytes_file.is_file():
        fb = int(full_bytes_file.read_text().strip() or "0")
        full_row.update(
            {
                "exists": True,
                "total_dump_bytes": fb,
                "enough": True,
                "evidence_note": "reuse_full_fidelity_upper_bound",
                "full_ref": args.full_ref,
            }
        )
    else:
        full_row.update({"exists": False, "total_dump_bytes": 0, "enough": False})

    d_bytes = int(dyn_row.get("total_dump_bytes") or 0)
    f_bytes = int(full_row.get("total_dump_bytes") or 0)
    dyn_adj = dyn_row.get("total_dump_bytes_w_star_est")
    ratio = (100.0 * d_bytes / f_bytes) if f_bytes > 0 else None
    ratio_w = (100.0 * int(dyn_adj) / f_bytes) if (f_bytes > 0 and dyn_adj is not None) else None
    headline_pct = ratio_w if ratio_w is not None else ratio
    headline_note = "W*_content_est" if ratio_w is not None else "raw_dump"

    summary = {
        "experiment": "PR2_B_E3_RATIO",
        "case": args.case,
        "parent": str(parent),
        "dynamic_reuse_run": args.dynamic_reuse_run,
        "dynamic_arm": str(dyn_arm),
        "resident_rate": args.resident_rate,
        "w_star": args.w_star,
        "dynamic": {k: v for k, v in dyn_row.items() if k != "torch_trace_steps"},
        "full": full_row,
        "ratio_raw_pct": None if ratio is None else round(ratio, 2),
        "ratio_w_star_pct": None if ratio_w is None else round(ratio_w, 2),
        "headline_pct": None if headline_pct is None else round(headline_pct, 2),
        "headline_note": headline_note,
        "same_cover": bool(dyn_row.get("enough")) and bool(full_row.get("enough")),
        "v2_ref_headline_pct": args.v2_ref_pct,
    }

    Path(args.out_json).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_pr2_md(summary, Path(args.out_md), v2_ref_pct=args.v2_ref_pct)
    print(
        f"[pr2_e3] headline={summary['headline_pct']}% dense={dyn_row.get('torch_trace_dense_ranks')} "
        f"culprit={dyn_row.get('localize', {}).get('culprit_rank')} → {args.out_md}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
