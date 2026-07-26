#!/usr/bin/env python3
"""Pillar-C E2：对各常驻 rate 臂判「采集内容够不够归因 / 够不够触发」。

尺：
  - 主证：cpu.utilization_rss（P3-SW）窗内抬升或绝对值（与 E1-off / B D4 同尺）
  - 辅：SET↑ 是否 OK；torch_trace 行数（升详后应有密度）
  - 量：总落盘字节（probing_data 全树；禁止只报 cold）
  - 禁止：用训练 step_ms 把各臂判成同 D
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000


def parse_rss_query_txt(path: Path) -> list[tuple[int, int]]:
    """Parse dump_probing_sql query_p3sw_rss_window.txt → [(ts, rss_kb)]."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[int, int]] = []
    for line in text.splitlines():
        if "│" not in line:
            continue
        cells = [c.strip() for c in line.split("│") if c.strip()]
        if len(cells) < 3:
            continue
        if cells[0] in ("ts", "─") or set(cells[0]) <= {"─", "├", "┼", "┤"}:
            continue
        try:
            ts = int(cells[0])
            rss = int(float(cells[2]))
        except ValueError:
            continue
        out.append((ts, rss))
    return out


def judge_rss(series: list[tuple[int, int]]) -> dict[str, Any]:
    if not series:
        return {"ok": False, "note": "rss_dump_missing", "n": 0, "rise": 0, "max": 0}
    vals = [r for _, r in series]
    mx, mn = max(vals), min(vals)
    rise = mx - mn
    ok = mx >= RSS_ABS_THR_KB or rise >= RSS_RISE_THR_KB
    return {
        "ok": ok,
        "note": f"rise_kb={rise}:max_kb={mx}:n={len(vals)}",
        "n": len(vals),
        "rise": rise,
        "max": mx,
    }


def find_one(arm_dir: Path, name: str) -> Optional[Path]:
    hits = list(arm_dir.rglob(name))
    return hits[0] if hits else None


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


def parse_volume(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {"raw": text.strip()}
    for key in ("total_bytes", "cold_bytes", "hot_bytes", "other_bytes", "memc_bytes"):
        m = re.search(rf"{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    return out


def set_ok(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {"ok": False, "note": "set_upgrade.log_missing"}
    text = path.read_text(encoding="utf-8", errors="replace")
    ok = "SET_OK_WORKER" in text or ("SET_OK" in text and "SET_FAIL_ALL" not in text)
    lat = None
    m = re.search(r"SET_LATENCY_MS=(\d+)", text)
    if m:
        lat = int(m.group(1))
    pid = None
    m = re.search(r"SET_OK_WORKER pid=(\d+)", text)
    if m:
        pid = m.group(1)
    return {"ok": ok, "note": "SET_OK" if ok else "SET_FAIL", "latency_ms": lat, "pid": pid}


def score_arm(parent: Path, rate: str, case: str) -> dict[str, Any]:
    arm = parent / f"rate_{rate}"
    row: dict[str, Any] = {
        "rate": rate,
        "arm_dir": str(arm),
        "exists": arm.is_dir(),
        "case": case,
    }
    if not arm.is_dir():
        row["enough"] = False
        row["trigger_ok"] = False
        row["evidence"] = "arm_dir_missing"
        return row

    rss_path = find_one(arm, "query_p3sw_rss_window.txt")
    # fallback any cpu util dump
    if rss_path is None:
        for cand in arm.rglob("query_*.txt"):
            if "rss" in cand.name.lower() or "cpu" in cand.name.lower():
                rss_path = cand
                break
    series = parse_rss_query_txt(rss_path) if rss_path else []
    rss = judge_rss(series)
    row["rss"] = rss
    row["rss_path"] = str(rss_path) if rss_path else None

    set_path = find_one(arm, "set_upgrade.log")
    sinfo = set_ok(set_path)
    row["set"] = sinfo

    vol_path = find_one(arm, "volume_final.txt")
    vol = parse_volume(vol_path)
    pdata = arm / "probing_data"
    total = dir_bytes(pdata)
    if total == 0 and "other_bytes" in vol:
        # volume_final 里 other+cold 更接近总落盘
        total = int(vol.get("other_bytes", 0)) + int(vol.get("cold_bytes", 0)) + int(vol.get("hot_bytes", 0))
    if total == 0 and "total_bytes" in vol:
        # 旧 volume 把 total 当 cold；仍记下但标 warning
        total = int(vol["total_bytes"])
        row["volume_note"] = "total_bytes_may_be_cold_only"
    row["volume"] = vol
    row["total_dump_bytes"] = total
    row["probing_data_bytes"] = dir_bytes(pdata)

    # torch_trace 辅证：dump 文本行数 / memt 文件体量粗估
    tt_hits = list(arm.rglob("*torch_trace*"))
    row["torch_trace_files"] = len(tt_hits)
    row["torch_trace_bytes"] = sum(f.stat().st_size for f in tt_hits if f.is_file())

    # 够触发：周期小表（RSS）能支撑粗判；够归因：RSS 主证命中（host 泄漏）
    # SET↑ 成功 = 升详路径通（C0-a 已证）；rate=0 时 torch 平时空也 OK
    trigger_ok = rss["ok"]  # 粗判靠周期小表
    enough = rss["ok"] and sinfo["ok"]
    row["trigger_ok"] = trigger_ok
    row["enough"] = enough
    row["evidence"] = f"rss:{rss['note']};set:{sinfo['note']};dump_B={total}"
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--rates", nargs="+", required=True)
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    parent = Path(args.parent_local)
    rows = [score_arm(parent, r, args.case) for r in args.rates]

    # 够触发的最稀常驻率：rates 升序找第一个 trigger_ok
    sorted_rates = sorted(args.rates, key=lambda x: float(x))
    sparsest = None
    for r in sorted_rates:
        row = next(x for x in rows if x["rate"] == r)
        if row.get("trigger_ok"):
            sparsest = r
            break

    summary = {
        "case": args.case,
        "parent": str(parent),
        "sparsest_trigger_rate": sparsest,
        "rows": rows,
    }
    out_json = parent / "E2_RATE.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# E2_RATE · 平时多稀够不够触发",
        "",
        f"> case=`{args.case}` · parent=`{parent.name}`",
        "> **尺**：采集内容（`cpu.utilization_rss`）够不够粗判/归因；总落盘字节作量；**禁止**只用 cold；**禁止**训练 step_ms 判同 D。",
        "> 流程：常驻 `rate=R` → 注入 onset 附近 SET `on,rate=1.0` → dump。",
        "",
        f"## 结论：够触发的最稀常驻率 = **`{sparsest}`**",
        "",
        "| rate | trigger_ok | enough(RSS∧SET) | RSS evidence | SET | total_dump_B | tt_files |",
        "|------|------------|-----------------|--------------|-----|--------------|----------|",
    ]
    for row in rows:
        rss = row.get("rss") or {}
        sinfo = row.get("set") or {}
        lines.append(
            f"| {row['rate']} | {'Y' if row.get('trigger_ok') else 'N'} | "
            f"{'Y' if row.get('enough') else 'N'} | `{rss.get('note', '?')}` | "
            f"{sinfo.get('note', '?')} | {row.get('total_dump_bytes', 0)} | "
            f"{row.get('torch_trace_files', 0)} |"
        )
    lines += [
        "",
        "## 设计回哺",
        "",
        f"- 常驻默认采样率可取 **{sparsest}**（本首格 P3-SW-A loud；host 泄漏主证在周期 `cpu.utilization`，不依赖 torch_trace 常驻密度）。",
        "- `rate=0` 若 trigger_ok：印证「触发靠周期小表，torch_trace 平时可不写」。",
        "- 各臂 step_ms **不并比**；归因差只看本表采集列。",
        "",
        f"- JSON：`{out_json}`",
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"[e2_score] sparsest_trigger_rate={sparsest}")
    print(f"[e2_score] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
