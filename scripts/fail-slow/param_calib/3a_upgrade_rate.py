#!/usr/bin/env python3
"""Param-Calib ③-A：触发后升采样率 → D-level vs rate；定够 D4 的最小 rate*。

尺（采集侧，禁止训练 step_ms / 禁止只报 cold）：
  - P3-SW-A 主证：cpu.utilization_rss
  - 升详半截：SET probing.torch.profiling=on,rate=R 成功
  - 归因密度：python.torch_trace 行数（相对 rate=1.0 与绝对地板）
  - D 级：
      D0 无 RSS
      D2 RSS 够粗判（周期小表）
      D3 RSS∧SET（升详通路通，但 TT 稀疏不够根因）
      D4 RSS∧SET∧TT 够密（够归因）
  - rate≈0 端点可挂 E4（禁 SET → 期望 ≤D2）
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000
MAGIC_MEMT = 0x4D454D54
# 够 D4 的 TT 绝对地板（单 victim 模块帧；远低于 E3 全量 5e4）
TT_ABS_FLOOR = 800
# 相对 rate=1.0 臂：至少 5% 行数（极稀采样仍可能掉级）
TT_REL_FRAC = 0.05


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


def find_one(root: Path, name: str) -> Optional[Path]:
    hits = list(root.rglob(name))
    return hits[0] if hits else None


def parse_rss_query_txt(path: Path) -> list[tuple[int, int]]:
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


def set_status(path: Optional[Path], *, expect_absent: bool = False) -> dict[str, Any]:
    if not path or not path.is_file():
        return {
            "ok": False,
            "present": False,
            "note": "set_upgrade.log_absent" if expect_absent else "set_upgrade.log_missing",
            "target_rate": None,
            "n_ok_workers": 0,
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    n_ok = len(re.findall(r"SET_OK_WORKER", text))
    ok = n_ok > 0 and "SET_FAIL_ALL" not in text
    m = re.search(r"set_rate=([0-9.]+)|SET_TARGET=probing\.torch\.profiling=on,rate=([0-9.]+)", text)
    target = None
    if m:
        target = m.group(1) or m.group(2)
    lat = None
    lm = re.search(r"SET_LATENCY_MS=(\d+)", text)
    if lm:
        lat = int(lm.group(1))
    return {
        "ok": ok,
        "present": True,
        "note": f"SET_OK_n={n_ok}" if ok else ("SET_FAIL_ALL" if "SET_FAIL_ALL" in text else "SET_FAIL"),
        "target_rate": target,
        "n_ok_workers": n_ok,
        "latency_ms": lat,
    }


def memt_n_rows(path: Path) -> dict[str, Any]:
    try:
        buf = path.read_bytes()
    except OSError as e:
        return {"error": str(e), "file_bytes": 0, "n_rows": 0}
    if len(buf) < 64 or struct.unpack_from("<I", buf, 0)[0] != MAGIC_MEMT:
        return {"error": "not_memt", "file_bytes": len(buf), "n_rows": 0}
    _magic, _ver, _hsz, _bom, _ts, _flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    nrows = 0
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        if cs + 40 > len(buf):
            break
        _gen, used, row_count, state, _res, _min_ts, _max_ts = struct.unpack_from("<QIIIIqq", buf, cs)
        if row_count == 0 or used == 0:
            continue
        nrows += int(row_count)
    return {"file_bytes": len(buf), "n_rows": nrows, "nchunks": nchunks}


def resolve_pdata(arm: Path) -> Path:
    pdata = arm / "probing_data"
    if pdata.is_dir():
        return pdata
    hits = [p for p in arm.rglob("probing_data") if p.is_dir()]
    return hits[0] if hits else pdata


def score_arm(
    arm: Path,
    rate: str,
    *,
    expect_no_set: bool = False,
    hung: bool = False,
    hung_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "rate": rate,
        "arm_dir": str(arm),
        "exists": arm.is_dir(),
        "hung": hung,
        "expect_no_set": expect_no_set,
    }
    if hung and hung_meta:
        row.update(hung_meta)
        return row
    if not arm.is_dir():
        row.update({"rss_ok": False, "set_ok": False, "tt_enough": False, "d_level": 0, "enough_d4": False})
        return row

    pdata = resolve_pdata(arm)
    total = dir_bytes(pdata)
    cold_hits = list(pdata.rglob("*.memc")) if pdata.is_dir() else []
    cold = sum(f.stat().st_size for f in cold_hits if f.is_file())

    rss_path = find_one(arm, "query_p3sw_rss_window.txt")
    if rss_path is None:
        for cand in arm.rglob("query_*.txt"):
            if "rss" in cand.name.lower() or "cpu" in cand.name.lower():
                rss_path = cand
                break
    series = parse_rss_query_txt(rss_path) if rss_path else []
    rss = judge_rss(series)
    sinfo = set_status(find_one(arm, "set_upgrade.log"), expect_absent=expect_no_set)

    tt_files = list(pdata.rglob("python.torch_trace")) if pdata.is_dir() else []
    dense = []
    for f in tt_files:
        if not f.is_file():
            continue
        info = memt_n_rows(f)
        if int(info.get("n_rows") or 0) > 0:
            dense.append({"path": str(f), **info})
    dense.sort(key=lambda x: -int(x.get("n_rows") or 0))
    tt_rows_all = sum(int(x.get("n_rows") or 0) for x in dense)

    row.update(
        {
            "probing_data": str(pdata),
            "total_dump_bytes": total,
            "cold_bytes": cold,
            "rss": rss,
            "rss_ok": bool(rss.get("ok")),
            "rss_path": str(rss_path) if rss_path else None,
            "set": sinfo,
            "set_ok": bool(sinfo.get("ok")) and not expect_no_set,
            "torch_trace_files": len(tt_files),
            "torch_trace_dense_ranks": len(dense),
            "torch_trace_rows_all": tt_rows_all,
            "torch_trace_top": dense[:3],
        }
    )
    return row


def assign_d(
    row: dict[str, Any],
    *,
    ref_tt_rows: int,
) -> dict[str, Any]:
    rss_ok = bool(row.get("rss_ok"))
    set_ok = bool(row.get("set_ok"))
    tt = int(row.get("torch_trace_rows_all") or 0)
    floor = TT_ABS_FLOOR
    if ref_tt_rows > 0:
        floor = max(TT_ABS_FLOOR, int(ref_tt_rows * TT_REL_FRAC))
    tt_enough = tt >= floor and int(row.get("torch_trace_dense_ranks") or 0) >= 1
    if row.get("expect_no_set"):
        # 禁升详：即便 RSS 在，完整动态路径也不够 D4
        d = 2 if rss_ok else 0
        enough = False
    elif rss_ok and set_ok and tt_enough:
        d = 4
        enough = True
    elif rss_ok and set_ok:
        d = 3
        enough = False
    elif rss_ok:
        d = 2
        enough = False
    else:
        d = 0
        enough = False
    row["tt_floor_used"] = floor
    row["tt_enough"] = tt_enough
    row["d_level"] = d
    row["enough_d4"] = enough
    row["evidence"] = (
        f"rss:{'Y' if rss_ok else 'N'};set:{'Y' if set_ok else 'N'};"
        f"tt_rows={tt};tt_floor={floor};d={d}"
    )
    return row


def hung_e4_row(e4_local: Optional[Path], e4_note: str) -> dict[str, Any]:
    """E4 rate≈0 端点：优先读本地镜像/JSON；否则用 ledger 已知数。"""
    meta: dict[str, Any] = {
        "rate": "0",
        "hung": True,
        "expect_no_set": True,
        "source": e4_note,
        "rss_ok": True,
        "set_ok": False,
        "torch_trace_rows_all": 0,
        "torch_trace_dense_ranks": 0,
        "torch_trace_files": 0,
        "total_dump_bytes": None,
        "cold_bytes": None,
        "set": {"ok": False, "present": False, "note": "E4_forbid_SET"},
        "rss": {"ok": True, "note": "E4_hung_rss_Y"},
    }
    if e4_local and e4_local.is_file():
        try:
            data = json.loads(e4_local.read_text(encoding="utf-8"))
            naive = data.get("naive") or {}
            if isinstance(data.get("rows"), list):
                for r in data["rows"]:
                    if r.get("label") in ("naive", "naive_cut", "e4"):
                        naive = r
                        break
            if naive:
                rss = naive.get("rss") or {}
                meta["rss_ok"] = bool(naive.get("rss_ok", rss.get("ok", meta["rss_ok"])))
                meta["torch_trace_rows_all"] = int(naive.get("torch_trace_rows_all") or 0)
                meta["torch_trace_dense_ranks"] = int(naive.get("torch_trace_dense_ranks") or 0)
                meta["torch_trace_files"] = int(naive.get("torch_trace_files") or 0)
                meta["total_dump_bytes"] = naive.get("total_dump_bytes")
                meta["cold_bytes"] = naive.get("cold_bytes")
                meta["rss"] = rss or meta["rss"]
                meta["set"] = naive.get("set") or meta["set"]
                meta["source"] = str(e4_local)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    # ledger 默认锚：TT 0 vs E3 54054；RSS Y；禁 SET
    if meta["torch_trace_rows_all"] == 0 and "ledger" in e4_note:
        meta["torch_trace_rows_all"] = 0
        meta["rss_ok"] = True
    return score_arm(Path("."), "0", expect_no_set=True, hung=True, hung_meta=meta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--rates", nargs="+", required=True)
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--e4-ref", default="")
    ap.add_argument("--e3-ref", default="")
    ap.add_argument("--e4-json", default="")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for r in args.rates:
        arm = parent / f"upgrade_rate_{r}"
        rows.append(score_arm(arm, str(r)))

    # rate=1.0 作相对地板参考
    ref_tt = 0
    for row in rows:
        if str(row.get("rate")) in ("1.0", "1", "1.00"):
            ref_tt = int(row.get("torch_trace_rows_all") or 0)
            break
    if ref_tt <= 0 and rows:
        ref_tt = max(int(r.get("torch_trace_rows_all") or 0) for r in rows)

    e4_json = Path(args.e4_json) if args.e4_json else (parent / "E4_hung" / "E4_ABLATION.json")
    e4_row = hung_e4_row(
        e4_json if e4_json.is_file() else None,
        e4_note=f"ledger_E4_182630|{args.e4_ref}",
    )
    all_rows = [assign_d(e4_row, ref_tt_rows=ref_tt)] + [assign_d(r, ref_tt_rows=ref_tt) for r in rows]

    # 够 D4 的最小 rate（排除 hung 0）
    chosen = None
    for r in sorted(args.rates, key=lambda x: float(x)):
        row = next(x for x in all_rows if str(x["rate"]) == str(r))
        if row.get("enough_d4"):
            chosen = str(r)
            break

    supports = (
        f"对 {args.case} loud：触发后 SET `probing.torch.profiling=on,rate=R` 扫 "
        f"{list(args.rates)}（常驻 rate=0；SET@L≥100）。"
        f"rate≈0 挂 E4（TT=0，禁升详）作必要性端点。"
        f"够归因 D4 的最小 rate* = **{chosen}**"
        f"（判据=RSS∧SET∧TT≥max({TT_ABS_FLOOR}, {TT_REL_FRAC:.0%}×rate1.0)）。"
        "证明升精度是上根因层的必要机制，并标定升到多少够。"
    )

    param = {
        "param": "torch_trace_upgrade_rate",
        "exp_id": "3A_upgrade_rate",
        "swept_range": ["0", *list(args.rates)],
        "chosen_value": chosen,
        "choose_rule": f"min rate with enough_d4 (RSS∧SET∧TT≥max({TT_ABS_FLOOR},{TT_REL_FRAC}*rate1.0_rows))",
        "case": args.case,
        "controls": {
            "resident_rate": 0,
            "set_at_step": 100,
            "set_key": "probing.torch.profiling=",
            "inject_window": [100, 300],
            "victim_local_rank": 7,
            "forbid": ["training step_ms", "cold-only as volume", "multi independent vars"],
        },
        "ground_truth_source": {
            "inject_onset_victim": "inline recipe / ledger",
            "rate0_anchor": args.e4_ref or "E4_182630",
            "e3_ref": args.e3_ref or "",
        },
        "curve": [
            {
                "rate": r["rate"],
                "d_level": r.get("d_level"),
                "enough_d4": r.get("enough_d4"),
                "rss_ok": r.get("rss_ok"),
                "set_ok": r.get("set_ok"),
                "tt_rows": r.get("torch_trace_rows_all"),
                "tt_dense_ranks": r.get("torch_trace_dense_ranks"),
                "total_dump_bytes": r.get("total_dump_bytes"),
                "cold_bytes": r.get("cold_bytes"),
                "hung": r.get("hung", False),
                "evidence": r.get("evidence"),
            }
            for r in all_rows
        ],
        "rows_detail": all_rows,
        "supports_design": supports,
        "parent_run": parent.name,
        "scored_at": datetime.now().isoformat(timespec="seconds"),
    }

    (out_dir / "PARAM.json").write_text(json.dumps(param, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (parent / "PARAM.json").write_text(json.dumps(param, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# PARAM · ③-A 升采样率 D-level 增益",
        "",
        f"> case=`{args.case}` · parent=`{parent.name}` · 自变量=触发后 `probing.torch.profiling` rate",
        "> 尺：采集侧 RSS / SET / torch_trace；**禁止**训练 step_ms；**禁止**只报 cold。",
        f"> rate≈0 端点挂 E4（`{args.e4_ref or '182630'}`）；不作默认结论，只作曲线一端。",
        "",
        f"## 结论：够归因 D4 的最小 rate\\* = **`{chosen}`**",
        "",
        "| rate | D | enough_D4 | RSS | SET | TT rows | TT ranks | total_B | cold_B | note |",
        "|------|---|-----------|-----|-----|---------|----------|---------|--------|------|",
    ]
    for r in all_rows:
        note = "E4 hung" if r.get("hung") else (r.get("set") or {}).get("note", "")
        lines.append(
            f"| {r['rate']} | D{r.get('d_level')} | {'Y' if r.get('enough_d4') else 'N'} | "
            f"{'Y' if r.get('rss_ok') else 'N'} | {'Y' if r.get('set_ok') else 'N'} | "
            f"{r.get('torch_trace_rows_all')} | {r.get('torch_trace_dense_ranks')} | "
            f"{r.get('total_dump_bytes')} | {r.get('cold_bytes')} | {note} |"
        )
    lines += [
        "",
        "## 曲线要点",
        "",
        f"- rate=0（E4）：禁升详 → TT≈0 → **掉级**（D≤2）；证明「升精度是必要机制」。",
        f"- rate↑ → TT 密度↑ → D-level 升到 D4；rate\\*={chosen} 为首次够归因点。",
        f"- TT 地板 = max({TT_ABS_FLOOR}, {TT_REL_FRAC:.0%}×rate=1.0 行数)；本轮 ref_tt={ref_tt}。",
        "",
        "## 这数据证明为什么这么设",
        "",
        supports,
        "",
        "## 证据路径",
        "",
        f"- `{out_dir}/PARAM.json`",
        f"- `{parent}/`",
        f"- E4 锚：`{args.e4_ref}`",
        "",
    ]
    md = "\n".join(lines) + "\n"
    (out_dir / "PARAM.md").write_text(md, encoding="utf-8")
    (parent / "PARAM.md").write_text(md, encoding="utf-8")

    print(json.dumps({"chosen_value": chosen, "curve": param["curve"]}, ensure_ascii=False, indent=2))
    return 0 if chosen is not None else 1


if __name__ == "__main__":
    sys.exit(main())
